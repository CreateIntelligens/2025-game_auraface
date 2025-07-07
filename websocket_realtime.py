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
from datetime import datetime
from database_manager import PostgresFaceDatabase
from insightface.app import FaceAnalysis
from huggingface_hub import snapshot_download
import os

# 確保模型存在
if not os.path.exists("models/auraface"):
    print("正在下載 AuraFace 模型...")
    snapshot_download("fal/AuraFace-v1", local_dir="models/auraface")

# 初始化 AuraFace
print("初始化 AuraFace...")
face_app = FaceAnalysis(
    name="auraface",
    providers=["CPUExecutionProvider"],
    root=".",
)
face_app.prepare(ctx_id=0, det_size=(640, 640))

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
    
    async def register(self, websocket, path):
        """註冊新的 WebSocket 連接"""
        self.connected_clients.add(websocket)
        print(f"新客戶端連接: {websocket.remote_address}")
        
        try:
            await websocket.send(json.dumps({
                'type': 'connection_status',
                'status': 'connected',
                'message': '已連接到即時人臉識別系統'
            }))
            
            await self.handle_client(websocket)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.connected_clients.remove(websocket)
            print(f"客戶端斷線: {websocket.remote_address}")
    
    async def handle_client(self, websocket):
        """處理客戶端訊息"""
        async for message in websocket:
            try:
                data = json.loads(message)
                await self.process_message(websocket, data)
            except Exception as e:
                await websocket.send(json.dumps({
                    'type': 'error',
                    'message': f'訊息處理錯誤: {str(e)}'
                }))
    
    async def process_message(self, websocket, data):
        """處理不同類型的訊息"""
        message_type = data.get('type')
        
        if message_type == 'video_frame':
            await self.process_video_frame(websocket, data)
        elif message_type == 'get_stats':
            await self.send_stats(websocket)
        elif message_type == 'register_face':
            await self.register_new_face(websocket, data)
        else:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': f'未知訊息類型: {message_type}'
            }))
    
    async def process_video_frame(self, websocket, data):
        """處理視訊幀並進行人臉識別"""
        try:
            # 解析 base64 圖片
            image_data = data.get('image')
            if not image_data:
                return
            
            # 移除 data:image/jpeg;base64, 前綴
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            # 解碼圖片
            image_bytes = base64.b64decode(image_data)
            image = Image.open(io.BytesIO(image_bytes))
            
            # 轉換為 CV2 格式
            cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            
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
            
            # 在圖片上繪製結果
            annotated_image = self.draw_annotations(cv_image, results)
            
            # 轉換回 base64
            _, buffer = cv2.imencode('.jpg', annotated_image)
            annotated_b64 = base64.b64encode(buffer).decode('utf-8')
            
            # 發送結果
            response = {
                'type': 'recognition_result',
                'image': f'data:image/jpeg;base64,{annotated_b64}',
                'faces': results,
                'processing_time': round(processing_time * 1000, 2),  # ms
                'fps': round(1 / processing_time, 1) if processing_time > 0 else 0,
                'timestamp': datetime.now().isoformat()
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
            # 執行人臉檢測
            faces = face_app.get(cv_image)
            
            if not faces:
                return []
            
            results = []
            for face in faces:
                # 在資料庫中搜尋相似人臉
                matches = face_db.find_similar_faces(face.normed_embedding, threshold=0.6)
                
                if matches:
                    best_match = matches[0]
                    # 記錄識別日誌
                    face_db.log_recognition(
                        best_match['person_id'], 
                        best_match['name'], 
                        best_match['confidence'], 
                        "websocket_stream"
                    )
                    
                    results.append({
                        'bbox': face.bbox.tolist(),
                        'person_id': best_match['person_id'],
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
    
    async def register_new_face(self, websocket, data):
        """註冊新人臉"""
        try:
            name = data.get('name')
            role = data.get('role')
            department = data.get('department', '')
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
            
            if len(faces) > 1:
                await websocket.send(json.dumps({
                    'type': 'register_result',
                    'success': False,
                    'message': '檢測到多張人臉'
                }))
                return
            
            # 註冊人臉
            embedding = faces[0].normed_embedding
            success, message = face_db.register_face(name, role, department, embedding)
            
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

async def main():
    """啟動 WebSocket 伺服器"""
    recognizer = RealtimeFaceRecognition()
    
    print("🚀 啟動 WebSocket 即時人臉識別伺服器...")
    print("📡 WebSocket 伺服器位址: ws://localhost:8765")
    
    start_server = websockets.serve(
        recognizer.register, 
        "0.0.0.0", 
        8765,
        max_size=10 * 1024 * 1024,  # 10MB 消息大小限制
        ping_interval=20,
        ping_timeout=10
    )
    
    await start_server
    print("✅ WebSocket 伺服器已啟動")
    
    # 保持伺服器運行
    await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())