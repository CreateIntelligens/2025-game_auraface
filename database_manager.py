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
from pgvector.psycopg2 import register_vector

class PostgresFaceDatabase:
    def __init__(self, database_url=None):
        if database_url is None:
            database_url = os.getenv('DATABASE_URL', 
                'postgresql://auraface:auraface123@localhost:5432/auraface')
        
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
                'register_time': info['register_time'],
                'embedding': info['embedding'].tolist()
            }
        
        with open(self.database_file, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    
    def register_face(self, name, role, department, embedding):
        """註冊新人臉"""
        try:
            person_id = f"{role}_{len(self.get_all_faces()):04d}"
            
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                # JSON 模式
                self.faces[person_id] = {
                    'name': name,
                    'role': role,
                    'department': department,
                    'register_time': datetime.now().isoformat(),
                    'embedding': embedding
                }
                self.save_json_database()
                return True, f"成功註冊 {name}（ID: {person_id}）"
            
            # PostgreSQL 模式
            with self.conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO face_profiles (person_id, name, role, department, face_embedding)
                    VALUES (%s, %s, %s, %s, %s)
                """, (person_id, name, role, department, embedding.tolist()))
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
            return []
    
    def get_all_faces(self):
        """取得所有人臉資料"""
        try:
            if hasattr(self, 'use_postgres') and not self.use_postgres:
                return self.faces
            
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT person_id, name, role, department, register_time
                    FROM face_profiles
                    ORDER BY register_time DESC
                """)
                
                results = {}
                for row in cursor.fetchall():
                    results[row['person_id']] = {
                        'name': row['name'],
                        'role': row['role'],
                        'department': row['department'] or '',
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
