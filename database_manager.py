#!/usr/bin/env python3
"""
PostgreSQL + pgvector 資料庫管理器
高效能向量搜尋的人臉資料庫
"""

import os
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime
import pytz
from pgvector.psycopg2 import register_vector

class PostgresFaceDatabase:
    def __init__(self, database_url=None):
        if database_url is None:
            database_url = os.getenv('DATABASE_URL', 
                'postgresql://ai360:ai360@postgres:5432/auraface')
        
        self.database_url = database_url
        self.conn = None
        self.connect()
    
    def connect(self):
        """連接到 PostgreSQL 資料庫"""
        try:
            self.conn = psycopg2.connect(self.database_url)
            register_vector(self.conn)
            print("✅ PostgreSQL 連接成功")
        except Exception as e:
            print(f"❌ PostgreSQL 連接失敗: {e}")
            # 降級到 JSON 資料庫
            return self._fallback_to_json()
    
    def _fallback_to_json(self):
        """降級到 JSON 檔案資料庫"""
        print("🔄 降級使用 JSON 檔案資料庫")
        from pathlib import Path
        Path("database").mkdir(exist_ok=True)
        
        self.database_file = "database/faces.json"
        self.faces = self.load_json_database()
        self.use_postgres = False
        return True
    
    def load_json_database(self):
        """載入 JSON 資料庫"""
        if os.path.exists(self.database_file):
            with open(self.database_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for person_id in data:
                    data[person_id]['embedding'] = np.array(data[person_id]['embedding'])
                return data
        return {}
    
    def save_json_database(self):
        """儲存 JSON 資料庫"""
        data_to_save = {}
        for person_id, info in self.faces.items():
            data_to_save[person_id] = {
                'name': info['name'],
                'role': info['role'],
                'department': info.get('department', ''),
                'email': info.get('email', ''),
                'register_time': info['register_time'],
                'embedding': info['embedding'].tolist()
            }
        
        with open(self.database_file, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    
    def register_face(self, name, role, department, embedding, employee_id=None, email=None):
        """註冊新人臉"""
        try:
            # 生成唯一的 person_id，使用簡潔的英文格式
            import uuid
            unique_suffix = str(uuid.uuid4())[:8]  # 取UUID前8位
            existing_faces = self.get_all_faces()
            
            # 根據角色生成不同前綴
            if role == '員工':
                prefix = 'employee'
            elif role == '訪客':
                prefix = 'visitor'
            else:
                prefix = role.lower()
            
            # 確保 ID 唯一性
            while True:
                person_id = f"{prefix}_{unique_suffix}"
                if person_id not in existing_faces:
                    break
                unique_suffix = str(uuid.uuid4())[:8]  # 重新生成UUID
            
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式
                self.faces[person_id] = {
                    'name': name,
                    'role': role,
                    'department': department,
                    'employee_id': employee_id,
                    'email': email,
                    'register_time': datetime.now().isoformat(),
                    'embedding': embedding
                }
                self.save_json_database()
                return True, f"成功註冊 {name}（ID: {person_id}）"
            
            # PostgreSQL 模式
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO face_profiles (person_id, employee_id, name, role, department, email, face_embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (person_id, employee_id, name, role, department, email, embedding.tolist()))
                self.conn.commit()
            
            return True, f"成功註冊 {name}（ID: {person_id}）"
            
        except Exception as e:
            if self.conn:
                self.conn.rollback()
            return False, f"註冊失敗：{str(e)}"
    
    def find_similar_faces(self, query_embedding, threshold=0.6, limit=5):
        """尋找相似人臉"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式 - 線性搜尋
                results = []
                for person_id, info in self.faces.items():
                    similarity = np.dot(query_embedding, info['embedding'])
                    if similarity >= threshold:
                        results.append({
                            'person_id': person_id,
                            'name': info['name'],
                            'role': info['role'],
                            'department': info.get('department', ''),
                            'confidence': float(similarity)
                        })
                
                # 按相似度排序
                results.sort(key=lambda x: x['confidence'], reverse=True)
                return results[:limit]
            
            # PostgreSQL 模式 - 向量搜尋
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # 將 numpy array 轉換為 pgvector 格式
                embedding_str = '[' + ','.join(map(str, query_embedding.tolist())) + ']'
                cursor.execute("""
                    SELECT person_id, name, role, department,
                           (face_embedding <=> %s::vector) AS distance,
                           1 - (face_embedding <=> %s::vector) AS similarity
                    FROM face_profiles
                    WHERE 1 - (face_embedding <=> %s::vector) >= %s
                    ORDER BY face_embedding <=> %s::vector
                    LIMIT %s
                """, (embedding_str, embedding_str, embedding_str, threshold, embedding_str, limit))
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'person_id': row['person_id'],
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
                        'confidence': float(row['similarity'])
                    })
                
                return results
            
        except Exception as e:
            print(f"搜尋錯誤: {e}")
            # 重要：回滾事務以避免後續操作失敗
            try:
                if hasattr(self, 'conn') and self.conn:
                    self.conn.rollback()
            except:
                pass
            return []
    
    def get_person_by_id(self, person_id):
        """根據person_id獲取人員資料（包含embedding）"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return self.faces.get(person_id)
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT person_id, employee_id, name, role, department, email, register_time, face_embedding
                    FROM face_profiles
                    WHERE person_id = %s
                """, (person_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'person_id': row['person_id'],
                        'employee_id': row['employee_id'] or '',
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
                        'email': row['email'] or '',
                        'register_time': row['register_time'],
                        'embedding': row['face_embedding']
                    }
                return None
                
        except Exception as e:
            print(f"獲取人員資料錯誤: {e}")
            # 重要：回滾事務以避免後續操作失敗
            try:
                if hasattr(self, 'conn') and self.conn:
                    self.conn.rollback()
            except:
                pass
            return None

    def get_all_faces(self):
        """取得所有人臉資料"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return self.faces
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT person_id, employee_id, name, role, department, email, register_time
                    FROM face_profiles
                    ORDER BY register_time DESC
                """)
                
                results = {}
                for row in cursor.fetchall():
                    results[row['person_id']] = {
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
                        'employee_id': row['employee_id'] or '',
                        'email': row['email'] or '',
                        'register_time': row['register_time'].isoformat() if row['register_time'] else ''
                    }
                
                return results
            
        except Exception as e:
            print(f"查詢錯誤: {e}")
            return {}
    
    def get_statistics(self):
        """取得資料庫統計"""
        all_faces = self.get_all_faces()
        total_faces = len(all_faces)
        employees = sum(1 for info in all_faces.values() if info['role'] == '員工')
        visitors = total_faces - employees
        
        return {
            'total': total_faces,
            'employees': employees,
            'visitors': visitors
        }

    def update_face(self, person_id, name, employee_id, role, department, email=None):
        """更新現有人臉資料"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式
                if person_id in self.faces:
                    self.faces[person_id]['name'] = name
                    self.faces[person_id]['employee_id'] = employee_id
                    self.faces[person_id]['role'] = role
                    self.faces[person_id]['department'] = department
                    self.faces[person_id]['email'] = email
                    self.save_json_database()
                    return True, f"成功更新 {name}"
                return False, "找不到指定人員"

            # PostgreSQL 模式
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE face_profiles
                    SET name = %s, employee_id = %s, role = %s, department = %s, email = %s
                    WHERE person_id = %s
                """, (name, employee_id, role, department, email, person_id))
                self.conn.commit()
                if cursor.rowcount == 0:
                    return False, f"找不到 ID 為 {person_id} 的人員"
            return True, f"成功更新 ID {person_id} 的資料"
        except Exception as e:
            if self.conn:
                self.conn.rollback()
            return False, f"更新失敗：{str(e)}"

    def delete_face(self, person_id):
        """刪除指定人臉"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式
                if person_id in self.faces:
                    del self.faces[person_id]
                    self.save_json_database()
                    return True, f"成功刪除人員"
                return False, "找不到指定人員"

            # PostgreSQL 模式
            with self.conn.cursor() as cursor:
                # 也需要刪除相關的日誌和會話
                cursor.execute("DELETE FROM attendance_sessions WHERE person_id = %s", (person_id,))
                cursor.execute("DELETE FROM recognition_logs WHERE person_id = %s", (person_id,))
                cursor.execute("DELETE FROM face_profiles WHERE person_id = %s", (person_id,))
                self.conn.commit()
                if cursor.rowcount == 0:
                    return False, f"找不到 ID 為 {person_id} 的人員"
            return True, f"成功刪除 ID {person_id} 及其相關日誌和會話"
        except Exception as e:
            if self.conn:
                self.conn.rollback()
            return False, f"刪除失敗：{str(e)}"
    
    def log_recognition(self, person_id, recognized_name, confidence, image_source="unknown"):
        """記錄識別日誌"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式不記錄日誌
                return
            
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO recognition_logs (person_id, recognized_name, confidence, image_source)
                    VALUES (%s, %s, %s, %s)
                """, (person_id, recognized_name, confidence, image_source))
                self.conn.commit()
                
        except Exception as e:
            print(f"日誌記錄錯誤: {e}")

    def log_attendance(self, person_id):
        """記錄或更新一個出勤會話"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return  # JSON 模式不支援此功能

            taipei_tz = pytz.timezone('Asia/Taipei')
            now = datetime.now(taipei_tz)
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # 檢查是否有正在進行的會話
                cursor.execute("""
                    SELECT session_uuid FROM attendance_sessions
                    WHERE person_id = %s AND status = 'active'
                """, (person_id,))
                active_session = cursor.fetchone()

                if active_session:
                    # 如果有，只更新 last_seen_at
                    cursor.execute("""
                        UPDATE attendance_sessions
                        SET last_seen_at = %s
                        WHERE session_uuid = %s
                    """, (now, active_session['session_uuid']))
                    session_uuid = active_session['session_uuid']
                else:
                    # 如果沒有，則創建新會話
                    import uuid
                    session_uuid = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO attendance_sessions (session_uuid, person_id, arrival_time, last_seen_at, status)
                        VALUES (%s, %s, %s, %s, 'active')
                    """, (session_uuid, person_id, now, now))
                self.conn.commit()
                return session_uuid
        except Exception as e:
            print(f"出勤記錄錯誤: {e}")
            import traceback
            traceback.print_exc()
            if self.conn:
                self.conn.rollback()
            return None

    def end_timed_out_sessions(self, timeout_seconds=300):
        """結束超時的出勤會話"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return # JSON 模式不支援此功能

            with self.conn.cursor() as cursor:
                cursor.execute("""
                    UPDATE attendance_sessions
                    SET status = 'ended', departure_time = last_seen_at
                    WHERE status = 'active' AND (NOW() - last_seen_at) > INTERVAL '%s seconds'
                """, (timeout_seconds,))
                updated_rows = cursor.rowcount
                self.conn.commit()
                if updated_rows > 0:
                    print(f"結束了 {updated_rows} 個超時會話。")
        except Exception as e:
            print(f"結束超時會話時出錯: {e}")
            if self.conn:
                self.conn.rollback()
    
    def get_recent_attendees(self, minutes=10):
        """取得最近N分鐘內出現的人員"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return []
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT DISTINCT 
                        a.person_id,
                        f.name,
                        f.role,
                        f.department,
                        MAX(a.last_seen_at) as last_seen,
                        COUNT(a.id) as session_count
                    FROM attendance_sessions a
                    JOIN face_profiles f ON a.person_id = f.person_id
                    WHERE a.last_seen_at >= NOW() - INTERVAL '%s minutes'
                    GROUP BY a.person_id, f.name, f.role, f.department
                    ORDER BY last_seen DESC
                """, (minutes,))
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'person_id': row['person_id'],
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
                        'last_seen': row['last_seen'],
                        'session_count': row['session_count']
                    })
                return results
                
        except Exception as e:
            print(f"查詢最近出勤錯誤: {e}")
            return []

    def get_attendance_history(self, person_id=None, days=7):
        """取得出勤歷史記錄"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return []
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if person_id:
                    cursor.execute("""
                        SELECT 
                            a.session_uuid,
                            a.person_id,
                            f.name,
                            a.arrival_time,
                            a.last_seen_at,
                            a.departure_time,
                            a.status,
                            EXTRACT(EPOCH FROM (COALESCE(a.departure_time, a.last_seen_at) - a.arrival_time))/60 as duration_minutes
                        FROM attendance_sessions a
                        JOIN face_profiles f ON a.person_id = f.person_id
                        WHERE a.person_id = %s 
                        AND a.arrival_time >= NOW() - INTERVAL '%s days'
                        ORDER BY a.arrival_time DESC
                    """, (person_id, days))
                else:
                    cursor.execute("""
                        SELECT 
                            a.session_uuid,
                            a.person_id,
                            f.name,
                            a.arrival_time,
                            a.last_seen_at,
                            a.departure_time,
                            a.status,
                            EXTRACT(EPOCH FROM (COALESCE(a.departure_time, a.last_seen_at) - a.arrival_time))/60 as duration_minutes
                        FROM attendance_sessions a
                        JOIN face_profiles f ON a.person_id = f.person_id
                        WHERE a.arrival_time >= NOW() - INTERVAL '%s days'
                        ORDER BY a.arrival_time DESC
                    """, (days,))
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'session_uuid': row['session_uuid'],
                        'person_id': row['person_id'],
                        'name': row['name'],
                        'arrival_time': row['arrival_time'],
                        'last_seen_at': row['last_seen_at'],
                        'departure_time': row['departure_time'],
                        'status': row['status'],
                        'duration_minutes': float(row['duration_minutes']) if row['duration_minutes'] else 0
                    })
                return results
                
        except Exception as e:
            print(f"查詢出勤歷史錯誤: {e}")
            return []

    def get_attendance_summary(self, person_id):
        """取得某人的出勤統計摘要"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return {}
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        COUNT(*) as total_sessions,
                        MIN(arrival_time) as first_seen,
                        MAX(last_seen_at) as last_seen,
                        SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_sessions,
                        AVG(EXTRACT(EPOCH FROM (COALESCE(departure_time, last_seen_at) - arrival_time))/60) as avg_duration_minutes,
                        COUNT(DISTINCT DATE(arrival_time)) as unique_days
                    FROM attendance_sessions
                    WHERE person_id = %s
                """, (person_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'total_sessions': row['total_sessions'],
                        'first_seen': row['first_seen'],
                        'last_seen': row['last_seen'],
                        'active_sessions': row['active_sessions'],
                        'avg_duration_minutes': float(row['avg_duration_minutes']) if row['avg_duration_minutes'] else 0,
                        'unique_days': row['unique_days']
                    }
                return {}
                
        except Exception as e:
            print(f"查詢出勤統計錯誤: {e}")
            return {}

    def get_current_session(self, person_id):
        """取得指定人員的當前session信息"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return None
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        session_uuid,
                        person_id,
                        status,
                        arrival_time,
                        departure_time,
                        last_seen_at
                    FROM attendance_sessions
                    WHERE person_id = %s AND status = 'active'
                    ORDER BY arrival_time DESC
                    LIMIT 1
                """, (person_id,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'session_uuid': row['session_uuid'],
                        'person_id': row['person_id'],
                        'status': row['status'],
                        'arrival_time': row['arrival_time'].isoformat() if row['arrival_time'] else None,
                        'departure_time': row['departure_time'].isoformat() if row['departure_time'] else None,
                        'last_seen_at': row['last_seen_at'].isoformat() if row['last_seen_at'] else None
                    }
                return None
                
        except Exception as e:
            print(f"查詢當前session錯誤: {e}")
            return None

    def get_current_attendees(self):
        """取得目前在場的人員"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return []
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT 
                        a.session_uuid,
                        a.person_id,
                        f.name,
                        f.role,
                        f.department,
                        a.arrival_time,
                        a.last_seen_at,
                        EXTRACT(EPOCH FROM (NOW() - a.arrival_time))/60 as duration_minutes
                    FROM attendance_sessions a
                    JOIN face_profiles f ON a.person_id = f.person_id
                    WHERE a.status = 'active'
                    ORDER BY a.arrival_time
                """)
                
                results = []
                for row in cursor.fetchall():
                    results.append({
                        'session_uuid': row['session_uuid'],
                        'person_id': row['person_id'],
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
                        'arrival_time': row['arrival_time'],
                        'last_seen_at': row['last_seen_at'],
                        'duration_minutes': float(row['duration_minutes'])
                    })
                return results
                
        except Exception as e:
            print(f"查詢目前在場人員錯誤: {e}")
            return []

    def close(self):
        """關閉資料庫連接"""
        if self.conn:
            self.conn.close()

# 測試資料庫連接
def test_database():
    """測試資料庫連接和效能"""
    db = PostgresFaceDatabase()
    
    # 測試基本功能
    test_embedding = np.random.rand(512).astype(np.float32)
    
    # 測試註冊
    success, message = db.register_face("測試用戶", "員工", "IT部門", test_embedding)
    print(f"註冊測試: {message}")
    
    # 測試搜尋
    results = db.find_similar_faces(test_embedding)
    print(f"搜尋測試: 找到 {len(results)} 個結果")
    
    # 測試統計
    stats = db.get_statistics()
    print(f"統計: {stats}")
    
    db.close()

if __name__ == "__main__":
    test_database()
