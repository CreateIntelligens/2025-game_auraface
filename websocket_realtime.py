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
    face_app.prepare(ctx_id=0, det_size=(320, 320))
    print("✅ AuraFace (GPU) 初始化完成！")
except Exception as e:
    print(f"⚠️ GPU 初始化失敗，嘗試降級到 CPU: {e}")
    try:
        face_app = FaceAnalysis(
            name="auraface",
            root=".",
            providers=["CPUExecutionProvider"]
        )
        face_app.prepare(ctx_id=-1, det_size=(320, 320))
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
                        print(f"📝 記錄識別日誌: {best_match['name']} (信心度: {best_match['confidence']:.3f})")
                    
                    # 出勤更新：不受冷卻限制，每次識別都更新
                    if best_match['confidence'] >= 0.4:
                        face_db.log_attendance(person_id)
                    
                    results.append({
                        'bbox': face.bbox.tolist(),
                        'person_id': person_id,
                        'name': best_match['name'],
                        'role': best_match['role'],
                        'department': best_match['department'],
                        'confidence': best_match['confidence']
                    })
                else:
                    results.append({
                        'bbox': face.bbox.tolist(),
                        'person_id': 'unknown',
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
            
            # 根據身分選擇顏色和是否顯示標籤
            if result['person_id'] == 'unknown':
                # 未識別的人臉：紅色框，不顯示任何文字
                color = (0, 0, 255)  # 紅色
                show_label = False
            elif result['role'] == '員工':
                # 已識別員工：綠色框，顯示完整標籤
                color = (0, 255, 0)  # 綠色
                show_label = True
            elif result['role'] == '訪客':
                # 已識別訪客：黃色框，顯示完整標籤
                color = (0, 255, 255)  # 黃色
                show_label = True
            else:
                # 其他情況：紅色框，不顯示標籤
                color = (0, 0, 255)  # 紅色
                show_label = False
            
            # 畫人臉框
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            
            # 只有已識別的人臉才顯示標籤
            if show_label and result['person_id'] != 'unknown':
                # 準備標籤文字
                label = f"{result['name']}"
                # 將中文角色轉換為英文
                role_mapping = {'員工': 'Staff', '訪客': 'Visitor'}
                role_text = f"[{role_mapping.get(result['role'], result['role'])}]"
                conf_text = f"{result['confidence']:.2f}"
                
                # 計算標籤背景大小
                label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)[0]
                role_size = cv2.getTextSize(role_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
                max_width = max(label_size[0], role_size[0]) + 10
                
                # 畫標籤背景
                cv2.rectangle(annotated, (x1, y1-60), (x1 + max_width, y1), color, -1)
                
                # 畫文字
                cv2.putText(annotated, role_text, (x1 + 5, y1 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                cv2.putText(annotated, label, (x1 + 5, y1 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(annotated, conf_text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
        
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
    
    # 保持伺服器運行
    await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
