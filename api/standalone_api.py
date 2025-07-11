#!/usr/bin/env python3
"""
AuraFace 獨立 API 服務
"""

from fastapi import FastAPI, Query, HTTPException
import uvicorn
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone, timedelta
import os

app = FastAPI(
    title="AuraFace 出勤記錄 API",
    description="提供 JSON 格式的出勤數據查詢接口",
    version="1.0.0"
)

# 台北時區
taipei_tz = timezone(timedelta(hours=8))

def get_db_connection():
    """獲取資料庫連接"""
    database_url = os.getenv('DATABASE_URL', 'postgresql://ai360:ai360@postgres:5432/auraface')
    return psycopg2.connect(database_url)

@app.get("/api/attendance")
async def get_attendance(
    person_id: str = Query(None, description="人員ID"),
    name: str = Query(None, description="姓名模糊查詢"),
    minutes: int = Query(10, description="最近N分鐘內有活動的記錄 (基於最後出現時間)，設為0表示不限制時間", ge=0),
    limit: int = Query(50, description="查詢數量限制", ge=1, le=200)
):
    """獲取出勤記錄"""
    try:
        conn = get_db_connection()
        
        # 建立基礎 SQL 查詢
        base_query = """
            SELECT 
                p.name, p.department, p.role, p.employee_id, p.email,
                s.session_uuid, s.status, s.arrival_time, s.departure_time, s.last_seen_at, s.person_id
            FROM attendance_sessions s
            JOIN face_profiles p ON s.person_id = p.person_id
        """
        
        # 建立 WHERE 條件
        where_conditions = []
        params = []
        
        # 人員條件
        if person_id:
            where_conditions.append("s.person_id = %s")
            params.append(person_id)
        elif name:
            where_conditions.append("p.name ILIKE %s")
            params.append(f'%{name}%')
        
        # 時間範圍條件 (基於最後出現時間)
        if minutes > 0:
            where_conditions.append("s.last_seen_at >= NOW() - INTERVAL '%s minutes'")
            params.append(minutes)
        
        # 組合完整 SQL
        if where_conditions:
            full_query = base_query + " WHERE " + " AND ".join(where_conditions)
        else:
            full_query = base_query
        
        full_query += " ORDER BY s.last_seen_at DESC LIMIT %s"
        params.append(limit)
        
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(full_query, params)
            
            results = cursor.fetchall()
            attendance_records = []
            
            for row in results:
                arrival = row['arrival_time']
                departure = row['departure_time']
                last_seen = row['last_seen_at']
                
                # 確保時間都有時區資訊
                if arrival and arrival.tzinfo is None:
                    arrival = arrival.replace(tzinfo=taipei_tz)
                if departure and departure.tzinfo is None:
                    departure = departure.replace(tzinfo=taipei_tz)
                if last_seen and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=taipei_tz)
                
                # 計算時長
                duration_seconds = 0
                duration_str = ""
                
                if arrival and departure:
                    duration = departure - arrival
                    duration_seconds = int(duration.total_seconds())
                    hours, remainder = divmod(duration_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"{hours}時{minutes}分{seconds}秒"
                    elif minutes > 0:
                        duration_str = f"{minutes}分{seconds}秒"
                    else:
                        duration_str = f"{seconds}秒"
                elif arrival:
                    # 計算持續時間
                    now = datetime.now(taipei_tz)
                    duration = now - arrival
                    duration_seconds = int(duration.total_seconds())
                    hours, remainder = divmod(duration_seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"已持續 {hours}時{minutes}分"
                    else:
                        duration_str = f"已持續 {minutes}分鐘"
                
                attendance_records.append({
                    'session_uuid': row['session_uuid'],
                    'person_id': row['person_id'],
                    'name': row['name'],
                    'department': row['department'] or '未設定',
                    'role': row['role'],
                    'employee_id': row['employee_id'] or '',
                    'email': row['email'] or '',
                    'status': 'active' if row['status'] == 'active' else 'ended',
                    'status_text': '在席中' if row['status'] == 'active' else '已離開',
                    'arrival_time': arrival.isoformat() if arrival else None,
                    'arrival_time_formatted': arrival.strftime("%Y-%m-%d %H:%M:%S") if arrival else "",
                    'departure_time': departure.isoformat() if departure else None,
                    'departure_time_formatted': departure.strftime("%Y-%m-%d %H:%M:%S") if departure else "",
                    'last_seen_at': last_seen.isoformat() if last_seen else None,
                    'last_seen_formatted': last_seen.strftime("%Y-%m-%d %H:%M:%S") if last_seen else "",
                    'duration_seconds': duration_seconds,
                    'duration_text': duration_str
                })
        
        conn.close()
        
        return {
            'success': True,
            'data': attendance_records,
            'count': len(attendance_records),
            'query': {
                'person_id': person_id,
                'name': name,
                'minutes': minutes,
                'limit': limit
            },
            'generated_at': datetime.now(taipei_tz).isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
async def health_check():
    """健康檢查"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        conn.close()
        
        return {
            'success': True,
            'status': 'healthy',
            'database': 'connected',
            'timestamp': datetime.now(taipei_tz).isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("🔌 啟動獨立 FastAPI 服務在端口 7859...")
    uvicorn.run(app, host="0.0.0.0", port=7859, log_level="info")