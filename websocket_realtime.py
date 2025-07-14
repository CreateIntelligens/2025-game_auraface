#!/usr/bin/env python3
"""
WebSocket 即時人臉識別系統
支援攝像頭串流和即時辨識
"""

import asyncio
import websockets
import json
import base64
import cv2
import numpy as np
from PIL import Image
import io
import time
import hashlib
import uuid
import aiohttp
from datetime import datetime, timezone, timedelta
from database_manager import PostgresFaceDatabase
from insightface.app import FaceAnalysis
from huggingface_hub import snapshot_download
import os

# GPU 加速設定
os.environ['CUDA_VISIBLE_DEVICES'] = '0'  # 使用第一張 GPU
os.environ['OMP_NUM_THREADS'] = '1'        # 限制 OpenMP 線程減少 CPU 使用

# 設定台灣時區
TW_TZ = timezone(timedelta(hours=8))

# --- 等待 app.py 下載模型 ---
model_dir = "models/auraface"
required_models = ["glintr100.onnx", "scrfd_10g_bnkps.onnx", "genderage.onnx", "1k3d68.onnx", "2d106det.onnx"]

print("⏳ 等待模型準備...")
import time
while True:
    if os.path.exists(model_dir):
        missing = [m for m in required_models if not os.path.exists(os.path.join(model_dir, m))]
        if not missing:
            print("✅ 模型準備完成")
            break
        else:
            print(f"⏳ 仍在等待模型: {missing[:3]}...")
    else:
        print("⏳ 等待模型目錄創建...")
    
    time.sleep(5)

# --- 初始化 AuraFace ---
print("正在初始化 AuraFace...")
try:
    # 讓 insightface 自動從模型目錄載入模型
    face_app = FaceAnalysis(
        name="auraface",
        root=".", # root="." 會讓它尋找 ./models/auraface
        providers=[
            ("CUDAExecutionProvider", {
                'device_id': 0,
                'arena_extend_strategy': 'kSameAsRequested',
                'gpu_mem_limit': 2 * 1024 * 1024 * 1024,  # 2GB
                'cudnn_conv_algo_search': 'EXHAUSTIVE',
            }),
            "CPUExecutionProvider"
        ]
    )
    face_app.prepare(ctx_id=0, det_size=(640, 640))  # 高畫質AI處理
    print("✅ AuraFace (GPU) 初始化完成！")
except Exception as e:
    print(f"⚠️ GPU 初始化失敗，嘗試降級到 CPU: {e}")
    try:
        face_app = FaceAnalysis(
            name="auraface",
            root=".",
            providers=["CPUExecutionProvider"]
        )
        face_app.prepare(ctx_id=-1, det_size=(640, 640))  # 高畫質AI處理
        print("✅ AuraFace (CPU) 初始化完成！")
    except Exception as cpu_e:
        print(f"❌ CPU 初始化也失敗了: {cpu_e}")
        print("請檢查模型檔案是否正確，以及 ONNX runtime 是否安裝成功。")
        exit(1)

# 驗證 GPU 使用
try:
    import onnxruntime as ort
    available_providers = ort.get_available_providers()
    print(f"🔍 可用提供者: {available_providers}")
    if 'CUDAExecutionProvider' in available_providers:
        print("✅ CUDA 加速已啟用")
    else:
        print("❌ CUDA 加速未啟用，請檢查 GPU 設定")
except ImportError:
    print("⚠️ onnxruntime 模組不可用")

# 初始化資料庫
face_db = PostgresFaceDatabase()

class RealtimeFaceRecognition:
    def __init__(self):
        self.connected_clients = set()
        self.recognition_stats = {
            'total_frames': 0,
            'faces_detected': 0,
            'employees_detected': 0,
            'visitors_detected': 0,
            'unknown_detected': 0
        }
        # 智能採樣控制（CPU 優化）
        self.client_frame_counters = {}  # 每個客戶端的幀計數器
        self.skip_frames = 4  # 跳過幀數：每5幀處理1幀（降低 CPU 負載）
        
        # 資料庫寫入控制（避免重複寫入）
        self.recent_recognitions = {}  # {person_id: last_recognition_time}
        self.recognition_cooldown = 10  # 同一人10秒內不重複寫入識別日誌
        
        # 智能通知機制
        self.person_detection_history = {}  # {person_id: [detection_times]}
        self.person_notification_times = {}  # {person_id: [notification_times]}
        self.stable_detection_count = 3      # 需要連續3次穩定識別
        self.first_notification_interval = 60   # 首次通知後1分鐘
        self.regular_notification_interval = 300  # 之後每5分鐘
        
        # 陌生人追蹤和去重機制
        self.stranger_faces = {}  # {face_hash: {'uuid': str, 'first_seen': datetime, 'last_seen': datetime, 'embedding': np.array}}
        self.stranger_cooldown = 900  # 15分鐘冷卻期（900秒）
        
        # 分流Webhook配置
        self.employee_webhook_url = os.getenv('EMPLOYEE_WEBHOOK_URL', 'http://host.docker.internal:8001/webhook/employee-detected')
        self.stranger_webhook_url = os.getenv('STRANGER_WEBHOOK_URL', 'http://host.docker.internal:8002/webhook/stranger-detected')
        
        # 陌生人確認機制（防止員工誤判）
        self.stranger_candidates = {}  # {face_hash: {'detections': [timestamps], 'embedding': np.array}}
        self.stranger_confirm_threshold = 5  # 連續5次檢測才確認是陌生人
        self.stranger_confirm_window = 30  # 30秒內的檢測
        self.recent_success_window = 30  # 30秒內有成功識別就不算陌生人
        
        # 臨時訪客管理
        self.temp_visitors = {}  # {person_id: {'registered_time': datetime, 'embedding': np.array}}
        self.temp_visitor_timeout = 300  # 5分鐘無活動後清理
    
    async def register(self, websocket, path):
        """註冊新的 WebSocket 連接"""
        self.connected_clients.add(websocket)
        print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 新客戶端連接: {websocket.remote_address}")
        
        try:
            await websocket.send(json.dumps({
                'type': 'connection_status',
                'status': 'connected',
                'message': '已連接到即時人臉識別系統',
                'timestamp': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
            }))
            
            await self.handle_client(websocket)
        except websockets.exceptions.ConnectionClosed as e:
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 客戶端正常斷線: {websocket.remote_address}")
        except Exception as e:
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 客戶端異常斷線: {websocket.remote_address}, 錯誤: {e}")
        finally:
            if websocket in self.connected_clients:
                self.connected_clients.remove(websocket)
            # 清理該客戶端的幀計數器
            client_id = id(websocket)
            if client_id in self.client_frame_counters:
                del self.client_frame_counters[client_id]
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 客戶端清理完成: {websocket.remote_address}")
    
    async def handle_client(self, websocket):
        """處理客戶端訊息"""
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.process_message(websocket, data)
                except json.JSONDecodeError as e:
                    print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] JSON 解析錯誤: {e}")
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': f'JSON 格式錯誤: {str(e)}'
                    }))
                except Exception as e:
                    print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 訊息處理錯誤: {e}")
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': f'訊息處理錯誤: {str(e)}'
                    }))
        except websockets.exceptions.ConnectionClosed:
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 客戶端連接已關閉")
        except Exception as e:
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] handle_client 錯誤: {e}")
    
    async def process_message(self, websocket, data):
        """處理不同類型的訊息"""
        message_type = data.get('type')
        
        if message_type == 'video_frame':
            await self.process_video_frame(websocket, data)
        elif message_type == 'get_stats':
            await self.send_stats(websocket)
        elif message_type == 'register_face':
            await self.register_new_face(websocket, data)
        elif message_type == 'get_persons':
            await self.get_all_persons(websocket)
        elif message_type == 'update_person':
            await self.update_person(websocket, data)
        elif message_type == 'delete_person':
            await self.delete_person(websocket, data)
        elif message_type == 'get_attendance':
            await self.get_attendance_logs(websocket)
        elif message_type == 'clear_attendance':
            await self.clear_attendance_logs(websocket)
        else:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'未知訊息類型: {message_type}'
            }))

    async def get_all_persons(self, websocket):
        """取得所有已註冊人員列表並發送給客戶端"""
        try:
            all_faces = face_db.get_all_faces()
            person_list = [{"person_id": pid, **pinfo} for pid, pinfo in all_faces.items()]
            await websocket.send(json.dumps({
                'type': 'persons_list',
                'success': True,
                'data': person_list
            }))
        except Exception as e:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'獲取人員列表失敗: {str(e)}'
            }))

    async def update_person(self, websocket, data):
        """更新人員資料"""
        try:
            person_id = data.get('person_id')
            name = data.get('name')
            employee_id = data.get('employee_id')
            role = data.get('role')
            department = data.get('department')
            email = data.get('email', '')
            
            if not all([person_id, name, role, department is not None]):
                await websocket.send(json.dumps({'type': 'update_result', 'success': False, 'message': '缺少必要資料'}))
                return

            success, message = face_db.update_face(person_id, name, employee_id, role, department, email)
            await websocket.send(json.dumps({
                'type': 'update_result',
                'success': success,
                'message': message
            }))
        except Exception as e:
            await websocket.send(json.dumps({'type': 'update_result', 'success': False, 'message': f'更新失敗: {str(e)}'}))

    async def delete_person(self, websocket, data):
        """刪除人員資料"""
        try:
            person_id = data.get('person_id')
            if not person_id:
                await websocket.send(json.dumps({'type': 'delete_result', 'success': False, 'message': '缺少 person_id'}))
                return

            success, message = face_db.delete_face(person_id)
            await websocket.send(json.dumps({
                'type': 'delete_result',
                'success': success,
                'message': message
            }))
        except Exception as e:
            await websocket.send(json.dumps({'type': 'delete_result', 'success': False, 'message': f'刪除失敗: {str(e)}'}))
    
    async def process_video_frame(self, websocket, data):
        """處理視訊幀並進行人臉識別"""
        try:
            # 智能採樣：跳過幀以減少GPU負載
            client_id = id(websocket)
            if client_id not in self.client_frame_counters:
                self.client_frame_counters[client_id] = 0
            
            self.client_frame_counters[client_id] += 1
            
            # 每4幀處理1幀，跳過其他幀
            if self.client_frame_counters[client_id] % (self.skip_frames + 1) != 0:
                # 跳過此幀，不進行處理
                return
            
            # 解析 base64 圖片
            image_data = data.get('image')
            if not image_data:
                return
            
            # 移除 data:image/jpeg;base64, 前綴
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            # 直接解碼為 numpy array，完全跳過 PIL 轉換
            image_bytes = base64.b64decode(image_data)
            nparr = np.frombuffer(image_bytes, np.uint8)
            cv_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # 進行人臉識別
            start_time = time.time()
            results = await self.identify_faces_async(cv_image)
            processing_time = time.time() - start_time
            
            # 更新統計
            self.recognition_stats['total_frames'] += 1
            if results:
                self.recognition_stats['faces_detected'] += len(results)
                for result in results:
                    if result['role'] == '員工':
                        self.recognition_stats['employees_detected'] += 1
                    elif result['person_id'] == 'unknown':
                        self.recognition_stats['unknown_detected'] += 1
                    else:
                        self.recognition_stats['visitors_detected'] += 1
            
            # 發送結果（不包含處理後的圖片，讓前端自己繪製）
            response = {
                'type': 'recognition_result',
                'faces': results,
                'processing_time': round(processing_time * 1000, 2),  # ms
                'fps': round(1 / processing_time, 1) if processing_time > 0 else 0,
                'timestamp': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'image_dimensions': {
                    'width': cv_image.shape[1],
                    'height': cv_image.shape[0]
                },
                'client_timestamp': data.get('client_timestamp')  # 回傳客戶端時間戳用於延遲計算
            }
            
            await websocket.send(json.dumps(response))
            
        except Exception as e:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'圖片處理錯誤: {str(e)}'
            }))
    
    async def identify_faces_async(self, cv_image):
        """非同步人臉識別"""
        try:
            # 極大幅降低處理解析度，從480降到256，最大化GPU利用率
            height, width = cv_image.shape[:2]
            if width > 256:
                scale = 256 / width
                new_width = 256
                new_height = int(height * scale)
                # 使用最快的插值演算法減少 CPU 負載
                cv_image = cv2.resize(cv_image, (new_width, new_height), interpolation=cv2.INTER_NEAREST)
                scale_factor = 1 / scale
            else:
                scale_factor = 1.0
            
            # 執行人臉檢測（GPU加速）
            faces = face_app.get(cv_image)
            
            if not faces:
                return []
            
            results = []
            current_time = time.time()
            
            for face in faces:
                # 調整座標回原始尺寸
                if scale_factor != 1.0:
                    face.bbox = face.bbox * scale_factor
                
                # 在資料庫中搜尋相似人臉，使用平衡的閾值
                matches = face_db.find_similar_faces(face.normed_embedding, threshold=0.4)
                
                if matches:
                    best_match = matches[0]
                    person_id = best_match['person_id']
                    
                    # 智能通知機制：穩定識別確認
                    await self.handle_person_detection(person_id, best_match, current_time)
                    
                    # 方案2：分離識別日誌和出勤更新
                    
                    # 識別日誌：10秒冷卻，統一門檻0.4
                    should_log_recognition = False
                    if person_id not in self.recent_recognitions:
                        should_log_recognition = True
                    else:
                        last_time = self.recent_recognitions[person_id]
                        if current_time - last_time > self.recognition_cooldown:
                            should_log_recognition = True
                    
                    # 寫入識別日誌（受冷卻限制）
                    if should_log_recognition and best_match['confidence'] >= 0.4:
                        face_db.log_recognition(
                            person_id, 
                            best_match['name'], 
                            best_match['confidence'], 
                            "websocket_stream"
                        )
                        self.recent_recognitions[person_id] = current_time
                        # 獲取session_uuid用於日誌顯示
                        session_info = face_db.get_current_session(person_id)
                        session_uuid = session_info.get("session_uuid") if session_info else "無session"
                        print(f"📝 記錄識別日誌: {best_match['name']} (信心度: {best_match['confidence']:.3f}, UUID: {session_uuid})")
                    
                    # 出勤更新：不受冷卻限制，每次識別都更新
                    is_new_session = False
                    if best_match['confidence'] >= 0.4:
                        # 先檢查是否已有活躍session
                        current_session = face_db.get_current_session(person_id)
                        if not current_session:
                            is_new_session = True
                            print(f"📢 檢測到新進場: {best_match['name']}")
                        
                        # 更新/建立attendance session
                        session_uuid = face_db.log_attendance(person_id)
                        
                        # 只有新session才發送webhook
                        if is_new_session and session_uuid:
                            # 根據role決定推送到哪個webhook
                            if best_match['role'] == '訪客':
                                # 訪客推送到陌生人webhook
                                visitor_data = {
                                    'event': 'temp_visitor_detected',
                                    'session_uuid': session_uuid,
                                    'person_id': best_match['person_id'],
                                    'name': best_match['name'],
                                    'department': best_match['department'],
                                    'role': best_match['role'],
                                    'employee_id': '',
                                    'email': '',
                                    'status': 'active',
                                    'status_text': '已註冊訪客',
                                    'arrival_time': datetime.now(TW_TZ).isoformat(),
                                    'last_seen_at': datetime.now(TW_TZ).isoformat(),
                                    'timestamp': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                                    'camera_id': 'websocket_stream',
                                    'confidence': best_match['confidence']
                                }
                                await self.send_stranger_webhook(visitor_data)
                            else:
                                # 員工推送到員工webhook
                                await self.send_employee_webhook(best_match, "detected")
                    
                    # 清除可能的陌生人候選（員工從遠處走近的情況）
                    self.clear_related_stranger_candidates(face.normed_embedding)
                    
                    results.append({
                        'bbox': face.bbox.tolist(),
                        'person_id': person_id,
                        'name': best_match['name'],
                        'role': best_match['role'],
                        'department': best_match['department'],
                        'confidence': best_match['confidence']
                    })
                else:
                    # 查詢最相似的人員（不設閾值，獲取信心度資訊）
                    all_matches = face_db.find_similar_faces(face.normed_embedding, threshold=0.0)
                    best_similarity = all_matches[0]['confidence'] if all_matches else 0.0
                    
                    # 如果有匹配但低於0.4閾值，根據信心度決定如何顯示
                    if all_matches:
                        best_match = all_matches[0]
                        confidence = best_match['confidence']
                        
                        if confidence >= 0.15:
                            # 0.15-0.39：顯示不確定信息，不顯示姓名
                            results.append({
                                'bbox': face.bbox.tolist(),
                                'person_id': 'uncertain',
                                'name': '',
                                'role': '',
                                'department': '',
                                'confidence': confidence,
                                'is_uncertain': True
                            })
                        else:
                            # <0.15：可能是陌生人，進行確認檢測
                            is_confirmed_stranger, face_hash = await self.confirm_stranger_detection(face.normed_embedding, current_time)
                            
                            if is_confirmed_stranger:
                                # 確認是陌生人，自動註冊為臨時訪客
                                temp_visitor_id, temp_visitor_name = await self.register_temp_visitor(face.normed_embedding, current_time)
                                
                                if temp_visitor_id:
                                    # 註冊成功 (attendance session已在register_temp_visitor中建立)
                                    
                                    results.append({
                                        'bbox': face.bbox.tolist(),
                                        'person_id': temp_visitor_id,
                                        'name': temp_visitor_name,
                                        'role': '訪客',
                                        'department': '臨時',
                                        'confidence': 0.99,  # 顯示高信心度，因為已經註冊
                                        'is_temp_visitor': True
                                    })
                                    
                                    # 清理候選記錄
                                    if face_hash in self.stranger_candidates:
                                        del self.stranger_candidates[face_hash]
                                else:
                                    # 註冊失敗，顯示為陌生人
                                    results.append({
                                        'bbox': face.bbox.tolist(),
                                        'person_id': 'unknown',
                                        'name': '',
                                        'role': '',
                                        'department': '',
                                        'confidence': confidence,
                                        'is_stranger': True,
                                        'best_match_confidence': confidence
                                    })
                            else:
                                # 還在確認階段，顯示為陌生人但不註冊
                                results.append({
                                    'bbox': face.bbox.tolist(),
                                    'person_id': 'unknown',
                                    'name': '',
                                    'role': '',
                                    'department': '',
                                    'confidence': confidence,
                                    'is_stranger': True,
                                    'best_match_confidence': confidence
                                })
                    else:
                        # 真正的陌生人（資料庫為空）
                        is_confirmed_stranger, face_hash = await self.confirm_stranger_detection(face.normed_embedding, current_time)
                        
                        if is_confirmed_stranger:
                            # 確認是陌生人，自動註冊為臨時訪客
                            temp_visitor_id, temp_visitor_name = await self.register_temp_visitor(face.normed_embedding, current_time)
                            
                            if temp_visitor_id:
                                # 註冊成功，建立attendance session
                                face_db.log_attendance(temp_visitor_id)
                                
                                results.append({
                                    'bbox': face.bbox.tolist(),
                                    'person_id': temp_visitor_id,
                                    'name': temp_visitor_name,
                                    'role': '訪客',
                                    'department': '臨時',
                                    'confidence': 0.99,  # 顯示高信心度，因為已經註冊
                                    'is_temp_visitor': True
                                })
                                
                                # 清理候選記錄
                                if face_hash in self.stranger_candidates:
                                    del self.stranger_candidates[face_hash]
                            else:
                                # 註冊失敗，顯示為陌生人
                                stranger_uuid = str(uuid.uuid4())
                                results.append({
                                    'bbox': face.bbox.tolist(),
                                    'person_id': stranger_uuid,
                                    'name': '',
                                    'role': '',
                                    'department': '',
                                    'confidence': 0.0
                                })
                        else:
                            # 還在確認階段，顯示為陌生人但不註冊
                            stranger_uuid = str(uuid.uuid4())
                            results.append({
                                'bbox': face.bbox.tolist(),
                                'person_id': stranger_uuid,
                                'name': '',
                                'role': '',
                                'department': '',
                                'confidence': 0.0
                            })
            
            return results
            
        except Exception as e:
            print(f"識別錯誤: {e}")
            return []
    
    def draw_annotations(self, cv_image, results):
        """在圖片上繪製識別結果"""
        annotated = cv_image.copy()
        
        for result in results:
            bbox = [int(x) for x in result['bbox']]
            x1, y1, x2, y2 = bbox
            
            # 根據信心度選擇顏色和標籤顯示方式
            confidence = result['confidence']
            
            # 除錯輸出
            print(f"🔍 DEBUG: 信心度 {confidence:.2f}, 姓名 {result.get('name', 'N/A')}, 角色 {result.get('role', 'N/A')}")
            
            if confidence >= 0.4:
                # 高信心度：綠色框，顯示完整標籤
                if result['role'] == '員工':
                    color = (0, 255, 0)  # 綠色
                elif result['role'] == '訪客':
                    color = (0, 255, 255)  # 黃色
                else:
                    color = (0, 255, 0)  # 綠色（預設）
                show_label = True
                label_type = 'full'  # 顯示姓名和角色
            elif confidence >= 0.15 or result.get('is_uncertain', False):
                # 中等信心度：橘色框，只顯示信心度
                print(f"🟠 DEBUG: 進入橘色框邏輯，信心度 {confidence:.2f}")
                color = (0, 165, 255)  # 橘色 (BGR格式)
                show_label = True
                label_type = 'confidence_only'  # 只顯示信心度
            else:
                # 低信心度：紅色框，顯示陌生人信息或低信心度
                print(f"🔴 DEBUG: 進入紅色框邏輯，信心度 {confidence:.2f}")
                color = (0, 0, 255)  # 紅色
                show_label = True
                if result.get('is_stranger', False) or result['person_id'] == 'unknown':
                    label_type = 'stranger'  # 顯示陌生人信息
                else:
                    label_type = 'confidence_only'  # 顯示低信心度
            
            # 畫人臉框
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            
            # 根據label_type顯示不同內容
            if show_label:
                if label_type == 'full':
                    # 高信心度：顯示完整信息
                    label = f"{result['name']}"
                    # 將中文角色轉換為英文
                    role_mapping = {'員工': 'Staff', '訪客': 'Visitor'}
                    role_text = f"[{role_mapping.get(result['role'], result['role'])}]"
                    conf_text = f"{result['confidence']:.2f}"
                    best_match_text = ""
                elif label_type == 'confidence_only':
                    # 中等信心度：只顯示信心度
                    label = "Uncertain"
                    role_text = "[Low Confidence]"
                    conf_text = f"{result['confidence']:.2f}"
                    best_match_text = ""
                else:  # label_type == 'stranger'
                    # 低信心度：顯示陌生人信息
                    label = "Unknown"
                    role_text = "[Stranger]"
                    conf_text = f"{result['confidence']:.2f}"
                    best_match_text = f"(vs {result.get('best_match_confidence', 0.0):.2f})"
                
                # 計算標籤背景大小
                label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
                role_size = cv2.getTextSize(role_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
                if best_match_text:
                    best_match_size = cv2.getTextSize(best_match_text, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)[0]
                    max_width = max(label_size[0], role_size[0], best_match_size[0]) + 10
                    background_height = 80  # 4行文字需要更高的背景
                else:
                    max_width = max(label_size[0], role_size[0]) + 10
                    background_height = 60  # 3行文字
                
                # 畫標籤背景
                cv2.rectangle(annotated, (x1, y1-background_height), (x1 + max_width, y1), color, -1)
                
                # 畫文字
                cv2.putText(annotated, role_text, (x1 + 5, y1 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                cv2.putText(annotated, label, (x1 + 5, y1 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(annotated, conf_text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
                
                # 如果是陌生人，顯示額外信息
                if best_match_text:
                    cv2.putText(annotated, best_match_text, (x1 + 5, y1 - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        
        return annotated
    
    async def send_stats(self, websocket):
        """發送統計資料"""
        response = {
            'type': 'stats',
            'data': self.recognition_stats
        }
        await websocket.send(json.dumps(response))
    
    async def handle_person_detection(self, person_id, best_match, current_time):
        """處理人員檢測的智能通知機制"""
        try:
            # 記錄檢測歷史
            if person_id not in self.person_detection_history:
                self.person_detection_history[person_id] = []
            
            self.person_detection_history[person_id].append(current_time)
            
            # 只保留最近的檢測記錄（避免記憶體洩漏）
            if len(self.person_detection_history[person_id]) > 10:
                self.person_detection_history[person_id] = self.person_detection_history[person_id][-10:]
            
            # 檢查是否達到穩定識別次數
            recent_detections = [t for t in self.person_detection_history[person_id] if current_time - t <= 10]  # 10秒內的檢測
            
            if len(recent_detections) >= self.stable_detection_count:
                # 檢查是否需要發送通知
                should_notify = False
                
                if person_id not in self.person_notification_times:
                    # 首次檢測到此人
                    should_notify = True
                    self.person_notification_times[person_id] = []
                else:
                    # 檢查距離上次通知的時間
                    last_notifications = self.person_notification_times[person_id]
                    if not last_notifications:
                        # 沒有通知記錄，發送首次通知
                        should_notify = True
                    else:
                        last_notification_time = last_notifications[-1]
                        time_since_last = current_time - last_notification_time
                        
                        # 根據通知次數決定間隔
                        if len(last_notifications) == 1:
                            # 第二次通知：首次通知後1分鐘
                            if time_since_last >= self.first_notification_interval:
                                should_notify = True
                        else:
                            # 後續通知：每5分鐘
                            if time_since_last >= self.regular_notification_interval:
                                should_notify = True
                
                # 發送通知
                if should_notify:
                    await self.send_person_detected_notification(person_id, best_match, current_time)
                    self.person_notification_times[person_id].append(current_time)
                    
                    # 只保留最近的通知記錄
                    if len(self.person_notification_times[person_id]) > 5:
                        self.person_notification_times[person_id] = self.person_notification_times[person_id][-5:]
        
        except Exception as e:
            print(f"處理人員檢測通知時發生錯誤: {e}")
    
    async def send_person_detected_notification(self, person_id, person_info, current_time):
        """發送人員檢測通知給所有連接的客戶端"""
        try:
            notification_count = len(self.person_notification_times.get(person_id, []))
            is_first_detection = notification_count == 0
            
            notification = {
                'type': 'person_detected',
                'person_id': person_id,
                'name': person_info['name'],
                'role': person_info['role'],
                'department': person_info['department'],
                'confidence': person_info['confidence'],
                'timestamp': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'first_detection': is_first_detection,
                'notification_count': notification_count + 1
            }
            
            # 發送給所有連接的客戶端
            if self.connected_clients:
                message = json.dumps(notification)
                await asyncio.gather(
                    *[client.send(message) for client in self.connected_clients],
                    return_exceptions=True
                )
                
                print(f"📢 發送人員檢測通知: {person_info['name']} ({'首次' if is_first_detection else f'第{notification_count + 1}次'})")
        
        except Exception as e:
            print(f"發送人員檢測通知時發生錯誤: {e}")

    def compute_face_similarity(self, embedding1, embedding2):
        """計算兩個人臉嵌入的相似度"""
        try:
            # 計算餘弦相似度
            dot_product = np.dot(embedding1, embedding2)
            norm1 = np.linalg.norm(embedding1)
            norm2 = np.linalg.norm(embedding2)
            similarity = dot_product / (norm1 * norm2)
            return similarity
        except Exception as e:
            print(f"計算人臉相似度時發生錯誤: {e}")
            return 0.0

    def generate_face_hash(self, face_embedding):
        """為人臉嵌入生成唯一哈希值"""
        try:
            # 將嵌入轉換為字符串然後生成哈希
            embedding_str = str(face_embedding.round(6))  # 四捨五入到6位小數
            return hashlib.md5(embedding_str.encode()).hexdigest()[:16]
        except Exception as e:
            print(f"生成人臉哈希時發生錯誤: {e}")
            return str(uuid.uuid4())[:16]

    def find_similar_stranger(self, face_embedding, threshold=0.8):
        """在已知陌生人中尋找相似的人臉"""
        try:
            current_time = time.time()
            
            # 清理過期的陌生人記錄
            expired_keys = []
            for face_hash, info in self.stranger_faces.items():
                if current_time - info['last_seen'] > self.stranger_cooldown:
                    expired_keys.append(face_hash)
            
            for key in expired_keys:
                del self.stranger_faces[key]
            
            # 尋找相似的陌生人
            for face_hash, info in self.stranger_faces.items():
                similarity = self.compute_face_similarity(face_embedding, info['embedding'])
                if similarity > threshold:
                    return face_hash, info
            
            return None, None
        except Exception as e:
            print(f"尋找相似陌生人時發生錯誤: {e}")
            return None, None

    async def send_employee_webhook(self, person_data, event_type="detected"):
        """發送員工識別webhook - 發送原始資料，讓webhook_receiver包裝API格式"""
        try:
            # 獲取當前session信息 (包含session_uuid)
            session_info = face_db.get_current_session(person_data["person_id"])
            
            # 組織員工資料，發送原始事件資料
            payload = {
                "event": f"employee_{event_type}",
                "session_uuid": session_info.get("session_uuid") if session_info else None,
                "person_id": person_data["person_id"],
                "name": person_data["name"],
                "department": person_data.get("department", "") or "未設定",
                "role": person_data["role"],
                "employee_id": person_data.get("employee_id", ""),
                "email": person_data.get("email", ""),
                "confidence": person_data["confidence"],
                "status": session_info.get("status", "active") if session_info else "active",
                "arrival_time": session_info.get("arrival_time") if session_info else None,
                "last_seen_at": session_info.get("last_seen_at") if session_info else None,
                "timestamp": datetime.now(TW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "camera_id": "websocket_stream"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.employee_webhook_url, json=payload, timeout=5) as response:
                    if response.status == 200:
                        print(f"✅ 員工識別推送成功: {person_data['name']} (UUID: {payload.get('session_uuid', 'N/A')})")
                    else:
                        print(f"❌ 員工識別推送失敗: {response.status}")
        except Exception as e:
            print(f"❌ 發送員工webhook時發生錯誤: {e}")

    async def send_stranger_webhook(self, stranger_data):
        """發送陌生人檢測webhook - 發送原始資料，讓webhook_receiver包裝API格式"""
        try:
            # 判斷事件類型
            event_type = stranger_data.get('event', 'stranger_detected')
            
            if event_type in ['stranger_auto_registered', 'temp_visitor_detected']:
                # 直接發送原始事件資料
                payload = stranger_data
            elif event_type == 'temp_visitor_departed':
                payload = {
                    "event": "temp_visitor_departed",
                    "temp_visitor_id": stranger_data["temp_visitor_id"],
                    "name": stranger_data["name"],
                    "timestamp": stranger_data["departure_time"],
                    "camera_id": "websocket_stream"
                }
            else:
                # 原本的陌生人檢測格式
                payload = {
                    "event": "stranger_detected",
                    "stranger_id": stranger_data["uuid"],
                    "timestamp": stranger_data["first_seen"].strftime("%Y-%m-%d %H:%M:%S"),
                    "camera_id": "websocket_stream",
                    "confidence": stranger_data.get("confidence", 0.0),
                    "best_match_confidence": stranger_data.get("best_match_confidence", 0.0)
                }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(self.stranger_webhook_url, json=payload, timeout=5) as response:
                    if response.status == 200:
                        print(f"✅ 陌生人事件推送成功: {event_type}")
                    else:
                        print(f"❌ 陌生人事件推送失敗: {response.status}")
        except Exception as e:
            print(f"❌ 發送陌生人webhook時發生錯誤: {e}")

    def check_recent_success_recognition(self, face_embedding, current_time):
        """檢查最近是否有成功識別記錄（防止員工誤判）"""
        try:
            # 檢查最近30秒內是否有成功識別
            cutoff_time = current_time - self.recent_success_window
            
            for person_id, last_time in self.recent_recognitions.items():
                if last_time > cutoff_time:
                    # 檢查是否是同一個人（通過embedding相似度）
                    try:
                        # 從資料庫獲取該人員的embedding
                        person_data = face_db.get_person_by_id(person_id)
                        if person_data and 'embedding' in person_data:
                            stored_embedding = np.array(person_data['embedding'])
                            similarity = self.compute_face_similarity(face_embedding, stored_embedding)
                            if similarity > 0.6:  # 相似度較高，可能是同一人
                                print(f"🔍 檢測到可能是員工誤判: {person_data.get('name', 'Unknown')} (相似度: {similarity:.3f})")
                                return True
                    except Exception as e:
                        print(f"檢查人員embedding時發生錯誤: {e}")
                        continue
            
            return False
        except Exception as e:
            print(f"檢查最近成功識別時發生錯誤: {e}")
            return False

    async def confirm_stranger_detection(self, face_embedding, current_time):
        """確認陌生人檢測（連續檢測機制）"""
        try:
            # 檢查最近是否有成功識別（防止員工誤判）
            if self.check_recent_success_recognition(face_embedding, current_time):
                print("🔍 最近有成功識別記錄，可能是員工誤判，不作為陌生人處理")
                return False, None
            
            # 尋找相似的已知陌生人候選
            similar_hash = None
            best_similarity = 0.0
            for existing_hash, candidate_info in self.stranger_candidates.items():
                similarity = np.dot(face_embedding, candidate_info['embedding'])
                if similarity > best_similarity:
                    best_similarity = similarity
                if similarity > 0.6:  # 降低到0.6閾值
                    similar_hash = existing_hash
                    print(f"🔍 找到相似陌生人候選 (相似度: {similarity:.3f})")
                    break
            
            if not similar_hash and best_similarity > 0:
                print(f"🔍 最高相似度: {best_similarity:.3f} (未達0.6閾值)")
            
            # 如果找到相似的，使用現有hash；否則生成新hash
            if similar_hash:
                face_hash = similar_hash
            else:
                face_hash = self.generate_face_hash(face_embedding)
                self.stranger_candidates[face_hash] = {
                    'detections': [],
                    'embedding': face_embedding.copy()
                }
            
            # 添加當前檢測時間
            self.stranger_candidates[face_hash]['detections'].append(current_time)
            
            # 清理過期的檢測記錄
            cutoff_time = current_time - self.stranger_confirm_window
            before_cleanup = len(self.stranger_candidates[face_hash]['detections'])
            self.stranger_candidates[face_hash]['detections'] = [
                t for t in self.stranger_candidates[face_hash]['detections'] 
                if t > cutoff_time
            ]
            after_cleanup = len(self.stranger_candidates[face_hash]['detections'])
            if before_cleanup > after_cleanup:
                print(f"🧹 清理過期檢測: {before_cleanup} → {after_cleanup} (窗口: {self.stranger_confirm_window}秒)")
            
            # 檢查是否達到確認閾值
            detection_count = len(self.stranger_candidates[face_hash]['detections'])
            print(f"🔍 陌生人候選檢測: {detection_count}/{self.stranger_confirm_threshold}")
            
            if detection_count >= self.stranger_confirm_threshold:
                print("✅ 確認為陌生人，準備自動註冊為訪客")
                return True, face_hash
            
            return False, None
            
        except Exception as e:
            print(f"確認陌生人檢測時發生錯誤: {e}")
            return False, None

    async def register_temp_visitor(self, face_embedding, current_time):
        """自動註冊陌生人為臨時訪客"""
        try:
            # 生成臨時訪客名稱
            temp_visitor_name = f"訪客_{datetime.now().strftime('%m%d_%H%M')}"
            
            # 註冊到資料庫 (讓系統自動生成person_id)
            success, message = face_db.register_face(
                name=temp_visitor_name,
                role="訪客",
                department="臨時",
                embedding=face_embedding,
                employee_id=None,
                email=""
            )
            
            if success:
                print(f"✅ 自動註冊臨時訪客: {temp_visitor_name}")
                
                # 從message解析person_id: "成功註冊 name（ID: person_id）"
                import re
                person_id_match = re.search(r'ID: ([^）]+)', message)
                if person_id_match:
                    temp_visitor_id = person_id_match.group(1)
                else:
                    temp_visitor_id = f"temp_visitor_{int(current_time)}"
                
                # 記錄到臨時訪客管理
                self.temp_visitors[temp_visitor_id] = {
                    'registered_time': current_time,
                    'embedding': face_embedding.copy(),
                    'name': temp_visitor_name
                }
                
                # 建立attendance session並取得UUID
                session_uuid = face_db.log_attendance(temp_visitor_id)
                
                # 發送webhook通知 (API格式)
                stranger_data = {
                    'event': 'stranger_auto_registered',
                    'session_uuid': session_uuid,
                    'person_id': temp_visitor_id,
                    'name': temp_visitor_name,
                    'department': '臨時',
                    'role': '訪客',
                    'employee_id': temp_visitor_id,
                    'email': '',
                    'status': 'active',
                    'status_text': '陌生訪客',
                    'arrival_time': datetime.now(TW_TZ).isoformat(),
                    'last_seen_at': datetime.now(TW_TZ).isoformat(),
                    'timestamp': datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                    'camera_id': 'websocket_stream',
                    'confidence': 0.12
                }
                await self.send_stranger_webhook(stranger_data)
                
                return temp_visitor_id, temp_visitor_name
            else:
                print(f"❌ 自動註冊失敗: {message}")
                return None, None
                
        except Exception as e:
            print(f"自動註冊臨時訪客時發生錯誤: {e}")
            return None, None

    async def cleanup_temp_visitors(self):
        """清理離開的臨時訪客"""
        try:
            current_time = time.time()
            visitors_to_remove = []
            
            for temp_visitor_id, visitor_info in self.temp_visitors.items():
                # 檢查最後活動時間
                try:
                    # 從attendance_sessions獲取最後活動時間
                    cursor = face_db.conn.cursor()
                    cursor.execute("""
                        SELECT last_seen_at FROM attendance_sessions 
                        WHERE person_id = %s AND status = 'active'
                        ORDER BY last_seen_at DESC LIMIT 1
                    """, (temp_visitor_id,))
                    
                    result = cursor.fetchone()
                    cursor.close()
                    
                    if result:
                        last_seen = result[0]
                        if last_seen:
                            # 轉換為時間戳
                            last_seen_timestamp = last_seen.timestamp()
                            
                            # 如果超過5分鐘沒有活動，準備清理
                            if current_time - last_seen_timestamp > self.temp_visitor_timeout:
                                visitors_to_remove.append(temp_visitor_id)
                                print(f"🧹 準備清理臨時訪客: {visitor_info['name']} (離開 {(current_time - last_seen_timestamp)/60:.1f} 分鐘)")
                except Exception as e:
                    print(f"檢查臨時訪客 {temp_visitor_id} 時發生錯誤: {e}")
            
            # 清理離開的臨時訪客
            for temp_visitor_id in visitors_to_remove:
                await self.remove_temp_visitor(temp_visitor_id)
                
        except Exception as e:
            print(f"清理臨時訪客時發生錯誤: {e}")

    async def remove_temp_visitor(self, temp_visitor_id):
        """移除臨時訪客的註冊記錄"""
        try:
            visitor_info = self.temp_visitors.get(temp_visitor_id)
            if not visitor_info:
                return
            
            # 結束attendance session
            cursor = face_db.conn.cursor()
            cursor.execute("""
                UPDATE attendance_sessions 
                SET status = 'ended', departure_time = CURRENT_TIMESTAMP
                WHERE person_id = %s AND status = 'active'
            """, (temp_visitor_id,))
            
            # 刪除人員註冊記錄
            cursor.execute("""
                DELETE FROM face_profiles WHERE person_id = %s
            """, (temp_visitor_id,))
            
            face_db.conn.commit()
            cursor.close()
            
            # 從記憶體中移除
            del self.temp_visitors[temp_visitor_id]
            
            print(f"🧹 已清理臨時訪客: {visitor_info['name']}")
            
            # 發送離開通知
            await self.send_stranger_webhook({
                'event': 'temp_visitor_departed',
                'temp_visitor_id': temp_visitor_id,
                'name': visitor_info['name'],
                'departure_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
            
        except Exception as e:
            print(f"移除臨時訪客時發生錯誤: {e}")

    def clear_related_stranger_candidates(self, face_embedding):
        """清除與當前人臉相關的陌生人候選（員工從遠處走近被正確識別後）"""
        try:
            to_remove = []
            for existing_hash, candidate_info in self.stranger_candidates.items():
                similarity = np.dot(face_embedding, candidate_info['embedding'])
                if similarity > 0.4:  # 與員工識別閾值一致
                    candidate_count = len(candidate_info['detections'])
                    print(f"🧹 清除相關陌生人候選: 相似度{similarity:.3f}, 已累積{candidate_count}/5")
                    to_remove.append(existing_hash)
            
            # 清除相關候選
            for hash_to_remove in to_remove:
                del self.stranger_candidates[hash_to_remove]
                
        except Exception as e:
            print(f"清除陌生人候選時發生錯誤: {e}")

    async def start_cleanup_task(self):
        """啟動清理任務"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分鐘檢查一次
                await self.cleanup_temp_visitors()
            except Exception as e:
                print(f"清理任務發生錯誤: {e}")
                await asyncio.sleep(60)

    async def handle_stranger_detection(self, face_embedding, current_time, confidence_info=None):
        """處理陌生人檢測和去重"""
        try:
            # 尋找相似的陌生人
            face_hash, stranger_info = self.find_similar_stranger(face_embedding)
            
            if stranger_info:
                # 更新已知陌生人的最後見到時間
                stranger_info['last_seen'] = current_time
                print(f"🔄 更新陌生人記錄: {stranger_info['uuid']}")
                return stranger_info['uuid'], False  # 返回UUID和是否為新陌生人
            else:
                # 發現新陌生人
                stranger_uuid = str(uuid.uuid4())
                face_hash = self.generate_face_hash(face_embedding)
                
                stranger_data = {
                    'uuid': stranger_uuid,
                    'first_seen': datetime.now(TW_TZ),
                    'last_seen': current_time,
                    'embedding': face_embedding.copy(),
                    'confidence': confidence_info.get('stranger_confidence', 0.0) if confidence_info else 0.0,
                    'best_match_confidence': confidence_info.get('best_match_confidence', 0.0) if confidence_info else 0.0
                }
                
                self.stranger_faces[face_hash] = stranger_data
                
                # 發送webhook通知
                await self.send_stranger_webhook(stranger_data)
                
                print(f"🆕 發現新陌生人: {stranger_uuid}")
                return stranger_uuid, True  # 返回UUID和是否為新陌生人
                
        except Exception as e:
            print(f"處理陌生人檢測時發生錯誤: {e}")
            return str(uuid.uuid4()), False

    async def register_new_face(self, websocket, data):
        """註冊新人臉"""
        try:
            name = data.get('name')
            role = data.get('role')
            department = data.get('department', '')
            employee_id = data.get('employee_id', '')
            email = data.get('email', '')
            image_data = data.get('image')
            
            if not all([name, role, image_data]):
                await websocket.send(json.dumps({
                    'type': 'register_result',
                    'success': False,
                    'message': '缺少必要資料'
                }))
                return
            
            # 解碼圖片
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
            cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            
            # 檢測人臉
            faces = face_app.get(cv_image)
            
            if len(faces) == 0:
                await websocket.send(json.dumps({
                    'type': 'register_result',
                    'success': False,
                    'message': '未檢測到人臉'
                }))
                return
            
            # 如果檢測到多張人臉，自動選擇最大的（通常是主要目標）
            if len(faces) > 1:
                print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 檢測到 {len(faces)} 張人臉，自動選擇最大的")
                # 按人臉大小排序，選擇最大的
                faces = sorted(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
                
                await websocket.send(json.dumps({
                    'type': 'register_info',
                    'message': f'檢測到 {len(faces)} 張人臉，已自動選擇最大的進行註冊'
                }))
            
            # 註冊最大的人臉
            embedding = faces[0].normed_embedding
            face_area = (faces[0].bbox[2] - faces[0].bbox[0]) * (faces[0].bbox[3] - faces[0].bbox[1])
            print(f"[{datetime.now(TW_TZ).strftime('%H:%M:%S')}] 註冊人臉大小: {face_area:.0f} 像素")
            success, message = face_db.register_face(name, role, department, embedding, employee_id, email)
            
            await websocket.send(json.dumps({
                'type': 'register_result',
                'success': success,
                'message': message
            }))
            
        except Exception as e:
            await websocket.send(json.dumps({
                'type': 'register_result',
                'success': False,
                'message': f'註冊錯誤: {str(e)}'
            }))

    async def get_attendance_logs(self, websocket):
        """取得出勤記錄"""
        try:
            import pytz
            taipei_tz = pytz.timezone('Asia/Taipei')
            
            if hasattr(face_db, 'use_postgres') and not face_db.use_postgres:
                await websocket.send(json.dumps({
                    'type': 'attendance_list',
                    'success': True,
                    'data': []
                }))
                return
            
            cursor = face_db.conn.cursor()
            cursor.execute("""
                SELECT 
                    p.name,
                    p.department,
                    p.role,
                    s.status,
                    s.arrival_time,
                    s.departure_time,
                    s.last_seen_at,
                    s.person_id
                FROM attendance_sessions s
                JOIN face_profiles p ON s.person_id = p.person_id
                ORDER BY s.last_seen_at DESC 
                LIMIT 100
            """)
            logs = cursor.fetchall()
            cursor.close()
            
            result = []
            for log in logs:
                name, department, role, status, arrival, departure, last_seen, person_id = log
                
                # 確保時間都有時區資訊
                if arrival and arrival.tzinfo is None:
                    arrival = taipei_tz.localize(arrival)
                if departure and departure.tzinfo is None:
                    departure = taipei_tz.localize(departure)
                if last_seen and last_seen.tzinfo is None:
                    last_seen = taipei_tz.localize(last_seen)
                
                # Format times
                arrival_str = arrival.strftime("%m-%d %H:%M:%S") if arrival else ""
                departure_str = departure.strftime("%m-%d %H:%M:%S") if departure else "在席中"
                last_seen_str = last_seen.strftime("%m-%d %H:%M:%S") if last_seen else ""
                
                # Calculate duration
                duration_str = ""
                if arrival and departure:
                    duration = departure - arrival
                    total_seconds = int(duration.total_seconds())
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"{hours}時{minutes}分"
                    elif minutes > 0:
                        duration_str = f"{minutes}分{seconds}秒"
                    else:
                        duration_str = f"{seconds}秒"
                elif arrival:
                    # Calculate ongoing duration
                    from datetime import datetime
                    now = datetime.now(taipei_tz)
                    duration = now - arrival
                    total_seconds = int(duration.total_seconds())
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"已持續 {hours}時{minutes}分"
                    else:
                        duration_str = f"已持續 {minutes}分鐘"

                result.append({
                    'name': name,
                    'status': "活躍" if status == 'active' else "結束",
                    'arrival_time': arrival_str,
                    'departure_time': departure_str,
                    'last_seen_time': last_seen_str,
                    'duration': duration_str,
                    'person_id': person_id,
                    'department': department or '未設定',
                    'role': role
                })
            
            await websocket.send(json.dumps({
                'type': 'attendance_list',
                'success': True,
                'data': result
            }))
            
        except Exception as e:
            print(f"取得出勤記錄錯誤: {e}")
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'取得出勤記錄失敗: {str(e)}'
            }))

    async def clear_attendance_logs(self, websocket):
        """清除出勤記錄"""
        try:
            if hasattr(face_db, 'use_postgres') and not face_db.use_postgres:
                await websocket.send(json.dumps({
                    'type': 'clear_attendance_result',
                    'success': False,
                    'message': 'JSON模式不支援清除功能'
                }))
                return
            
            cursor = face_db.conn.cursor()
            cursor.execute("DELETE FROM attendance_sessions")
            face_db.conn.commit()
            cursor.close()
            
            await websocket.send(json.dumps({
                'type': 'clear_attendance_result',
                'success': True,
                'message': '出勤記錄清除成功'
            }))
            
        except Exception as e:
            print(f"清除出勤記錄錯誤: {e}")
            await websocket.send(json.dumps({
                'type': 'clear_attendance_result',
                'success': False,
                'message': f'清除失敗: {str(e)}'
            }))

async def main():
    """啟動 WebSocket 伺服器"""
    recognizer = RealtimeFaceRecognition()
    
    # 從環境變數讀取端口
    ws_port = int(os.getenv('WEBSOCKET_PORT', 7861))
    
    print("🚀 啟動 WebSocket 即時人臉識別伺服器...")
    print(f"📡 WebSocket 伺服器位址: ws://localhost:{ws_port}")
    
    start_server = websockets.serve(
        recognizer.register, 
        "0.0.0.0", 
        ws_port,
        max_size=10 * 1024 * 1024,  # 10MB 消息大小限制
        ping_interval=None,         # 關閉自動心跳，改由客戶端處理
        ping_timeout=None,          # 關閉心跳超時
        close_timeout=10            # 10秒關閉超時
    )
    
    await start_server
    print("✅ WebSocket 伺服器已啟動")
    
    # 啟動清理任務
    cleanup_task = asyncio.create_task(recognizer.start_cleanup_task())
    print("🧹 臨時訪客清理任務已啟動")
    
    # 保持伺服器運行
    await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
