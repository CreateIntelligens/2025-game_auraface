#!/usr/bin/env python3
"""
AuraFace 出勤記錄 API 端點
在 Gradio 應用中提供 REST API 接口
"""

import json
from datetime import datetime, timezone, timedelta
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database_manager import PostgresFaceDatabase
import pytz

# 台北時區
taipei_tz = timezone(timedelta(hours=8))

def get_attendance_data_json(person_id=None, name=None, limit=50):
    """獲取出勤記錄並返回 JSON"""
    try:
        # 初始化資料庫連接
        db = PostgresFaceDatabase()
        
        if not hasattr(db, 'use_postgres') or not db.use_postgres:
            return {
                'success': False,
                'error': 'PostgreSQL 資料庫未啟用',
                'generated_at': datetime.now(taipei_tz).isoformat()
            }
        
        with db.conn.cursor() as cursor:
            if person_id:
                # 按人員ID查詢
                cursor.execute("""
                    SELECT 
                        p.name,
                        p.department,
                        p.role,
                        p.employee_id,
                        p.email,
                        s.status,
                        s.arrival_time,
                        s.departure_time,
                        s.last_seen_at,
                        s.person_id
                    FROM attendance_sessions s
                    JOIN face_profiles p ON s.person_id = p.person_id
                    WHERE s.person_id = %s
                    ORDER BY s.last_seen_at DESC 
                    LIMIT %s
                """, (person_id, limit))
            elif name:
                # 按姓名查詢
                cursor.execute("""
                    SELECT 
                        p.name,
                        p.department,
                        p.role,
                        p.employee_id,
                        p.email,
                        s.status,
                        s.arrival_time,
                        s.departure_time,
                        s.last_seen_at,
                        s.person_id
                    FROM attendance_sessions s
                    JOIN face_profiles p ON s.person_id = p.person_id
                    WHERE p.name ILIKE %s
                    ORDER BY s.last_seen_at DESC 
                    LIMIT %s
                """, (f'%{name}%', limit))
            else:
                # 獲取所有記錄
                cursor.execute("""
                    SELECT 
                        p.name,
                        p.department,
                        p.role,
                        p.employee_id,
                        p.email,
                        s.status,
                        s.arrival_time,
                        s.departure_time,
                        s.last_seen_at,
                        s.person_id
                    FROM attendance_sessions s
                    JOIN face_profiles p ON s.person_id = p.person_id
                    ORDER BY s.last_seen_at DESC 
                    LIMIT %s
                """, (limit,))
            
            results = cursor.fetchall()
            attendance_records = []
            
            for row in results:
                arrival, departure, last_seen = row[6], row[7], row[8]
                
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
                    'person_id': row[9],
                    'name': row[0],
                    'department': row[1] or '未設定',
                    'role': row[2],
                    'employee_id': row[3] or '',
                    'email': row[4] or '',
                    'status': 'active' if row[5] == 'active' else 'ended',
                    'status_text': '在席中' if row[5] == 'active' else '已離開',
                    'arrival_time': arrival.isoformat() if arrival else None,
                    'arrival_time_formatted': arrival.strftime("%Y-%m-%d %H:%M:%S") if arrival else "",
                    'departure_time': departure.isoformat() if departure else None,
                    'departure_time_formatted': departure.strftime("%Y-%m-%d %H:%M:%S") if departure else "",
                    'last_seen_at': last_seen.isoformat() if last_seen else None,
                    'last_seen_formatted': last_seen.strftime("%Y-%m-%d %H:%M:%S") if last_seen else "",
                    'duration_seconds': duration_seconds,
                    'duration_text': duration_str
                })
        
        db.close()
        
        return {
            'success': True,
            'data': attendance_records,
            'count': len(attendance_records),
            'query': {
                'person_id': person_id,
                'name': name,
                'limit': limit
            },
            'generated_at': datetime.now(taipei_tz).isoformat()
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'generated_at': datetime.now(taipei_tz).isoformat()
        }

def get_attendance_summary_json():
    """獲取出勤統計摘要"""
    try:
        db = PostgresFaceDatabase()
        
        if not hasattr(db, 'use_postgres') or not db.use_postgres:
            return {
                'success': False,
                'error': 'PostgreSQL 資料庫未啟用',
                'generated_at': datetime.now(taipei_tz).isoformat()
            }
        
        with db.conn.cursor() as cursor:
            # 活躍會話數
            cursor.execute("SELECT COUNT(*) FROM attendance_sessions WHERE status = 'active'")
            active_sessions = cursor.fetchone()[0]
            
            # 今日會話數
            cursor.execute("""
                SELECT COUNT(*) FROM attendance_sessions 
                WHERE DATE(arrival_time AT TIME ZONE 'Asia/Taipei') = CURRENT_DATE
            """)
            today_sessions = cursor.fetchone()[0]
            
            # 本週會話數
            cursor.execute("""
                SELECT COUNT(*) FROM attendance_sessions 
                WHERE arrival_time >= DATE_TRUNC('week', NOW() AT TIME ZONE 'Asia/Taipei')
            """)
            week_sessions = cursor.fetchone()[0]
            
            # 活躍人員列表
            cursor.execute("""
                SELECT p.name, p.role, s.arrival_time, s.last_seen_at
                FROM attendance_sessions s
                JOIN face_profiles p ON s.person_id = p.person_id
                WHERE s.status = 'active'
                ORDER BY s.arrival_time
            """)
            active_people = []
            for row in cursor.fetchall():
                arrival, last_seen = row[2], row[3]
                
                if arrival and arrival.tzinfo is None:
                    arrival = arrival.replace(tzinfo=taipei_tz)
                if last_seen and last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=taipei_tz)
                
                active_people.append({
                    'name': row[0],
                    'role': row[1],
                    'arrival_time': arrival.strftime("%H:%M:%S") if arrival else "",
                    'last_seen': last_seen.strftime("%H:%M:%S") if last_seen else ""
                })
        
        db.close()
        
        return {
            'success': True,
            'data': {
                'active_sessions': active_sessions,
                'today_sessions': today_sessions,
                'week_sessions': week_sessions,
                'active_people': active_people,
                'generated_at': datetime.now(taipei_tz).isoformat(),
                'timezone': 'Asia/Taipei'
            }
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'generated_at': datetime.now(taipei_tz).isoformat()
        }