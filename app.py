#!/usr/bin/env python3
"""
AuraFace 智能識別系統
商用人臉識別系統，支援 PostgreSQL + WebSocket 即時識別
"""

import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

import os
import cv2
import numpy as np
import gradio as gr
from PIL import Image, ImageDraw, ImageFont
import json
import time
from datetime import datetime
import pytz # 匯入 pytz
from huggingface_hub import snapshot_download
from insightface.app import FaceAnalysis
import threading
import queue
import tempfile
from pathlib import Path

# 建立必要目錄
os.makedirs("database", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# --- 模型檢查與下載 ---
model_dir = "models/auraface"
# 檢查核心模型文件 (根據 HuggingFace 實際文件列表)
required_models = ["glintr100.onnx", "scrfd_10g_bnkps.onnx", "genderage.onnx", "1k3d68.onnx", "2d106det.onnx"]

def check_models_complete():
    if not os.path.exists(model_dir):
        return False
    
    missing_models = []
    for model in required_models:
        model_path = os.path.join(model_dir, model)
        if not os.path.exists(model_path):
            missing_models.append(model)
        elif os.path.getsize(model_path) < 1024:  # 檢查文件大小 > 1KB
            missing_models.append(f"{model} (檔案損壞)")
    
    if missing_models:
        print(f"❌ 缺少或損壞的模型: {missing_models}")
        return False
    return True

if not check_models_complete():
    print(f"⚠️ 模型不完整，清理並重新下載...")
    # 清理舊的模型目錄
    if os.path.exists(model_dir):
        import shutil
        shutil.rmtree(model_dir)
    
    os.makedirs(model_dir, exist_ok=True)
    
    try:
        print("📥 開始下載 AuraFace 模型...")
        snapshot_download("fal/AuraFace-v1", local_dir=model_dir, local_dir_use_symlinks=False)
        print("✅ AuraFace 模型下載完成。")
        
        # 再次檢查
        if not check_models_complete():
            print("❌ 下載後仍有模型缺失")
            exit(1)
            
    except Exception as e:
        print(f"❌ 模型下載失敗: {e}")
        print("請檢查網路連線或 Hugging Face 連接")
        exit(1)
else:
    print("✅ AuraFace 模型已存在且完整。")

# --- 初始化 AuraFace ---
print("正在初始化 AuraFace...")
try:
    # 讓 insightface 自動從模型目錄載入模型
    app = FaceAnalysis(
        name="auraface",
        root=".", # root="." 會讓它尋找 ./models/auraface
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    app.prepare(ctx_id=0, det_size=(320, 320))
    print("✅ AuraFace (GPU) 初始化完成！")
except Exception as e:
    print(f"⚠️ GPU 初始化失敗，嘗試降級到 CPU: {e}")
    try:
        app = FaceAnalysis(
            name="auraface",
            root=".",
            providers=["CPUExecutionProvider"]
        )
        app.prepare(ctx_id=-1, det_size=(320, 320))
        print("✅ AuraFace (CPU) 初始化完成！")
    except Exception as cpu_e:
        print(f"❌ CPU 初始化也失敗了: {cpu_e}")
        print("請檢查模型檔案是否正確，以及 ONNX runtime 是否安裝成功。")
        exit(1)

# 匯入資料庫管理器
try:
    from database_manager import PostgresFaceDatabase
    print("✅ 載入 PostgreSQL 資料庫管理器")
except ImportError:
    print("❌ 無法載入 PostgreSQL 模組，使用基本資料庫")
    PostgresFaceDatabase = None

# 人臉資料庫
class FaceDatabase:
    def __init__(self):
        # 嘗試使用 PostgreSQL，失敗則降級到 JSON
        if PostgresFaceDatabase:
            try:
                self.db = PostgresFaceDatabase()
                self.use_postgres = True
                print("✅ 使用 PostgreSQL + pgvector 資料庫")
            except Exception as e:
                print(f"⚠️ PostgreSQL 連接失敗: {e}")
                self._init_json_db()
        else:
            self._init_json_db()
    
    def _init_json_db(self):
        """初始化 JSON 資料庫"""
        self.use_postgres = False
        self.database_file = "database/faces.json"
        self.faces = self.load_database()
        print("📁 使用 JSON 檔案資料庫")
    
    def load_database(self):
        """載入 JSON 資料庫"""
        if os.path.exists(self.database_file):
            with open(self.database_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 轉換 embedding 從 list 回 numpy array
                for person_id in data:
                    data[person_id]['embedding'] = np.array(data[person_id]['embedding'])
                    # 確保向下兼容，如果沒有 email 欄位則設為空字串
                    if 'email' not in data[person_id]:
                        data[person_id]['email'] = ''
                return data
        return {}
    
    def save_database(self):
        """儲存 JSON 資料庫"""
        if self.use_postgres:
            return  # PostgreSQL 自動儲存
            
        # 轉換 numpy array 為 list 以便 JSON 序列化
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
    
    def register_face(self, name, role, department, image, email=None):
        """註冊新人臉"""
        try:
            print(f"📝 開始註冊: {name} ({role})")
            
            # 轉換圖片格式
            cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            print(f"📷 註冊圖片尺寸: {cv_image.shape}")
            
            # 取得人臉特徵
            faces = app.get(cv_image)
            print(f"👤 註冊時檢測到 {len(faces)} 張人臉")
            
            if len(faces) == 0:
                return False, "未檢測到人臉"
            
            if len(faces) > 1:
                return False, "檢測到多張人臉，請確保圖片中只有一張人臉"
            
            embedding = faces[0].normed_embedding
            print(f"🧬 註冊特徵向量長度: {len(embedding)}")
            print(f"🧬 註冊特徵向量範圍: [{embedding.min():.3f}, {embedding.max():.3f}]")
            print(f"📦 註冊人臉框: {faces[0].bbox}")
            
            if self.use_postgres:
                # 使用 PostgreSQL
                result = self.db.register_face(name, role, department, embedding, email=email)
                print(f"✅ PostgreSQL 註冊結果: {result}")
                return result
            else:
                # 使用 JSON
                person_id = f"{role}_{len(self.faces):04d}"
                self.faces[person_id] = {
                    'name': name,
                    'role': role,
                    'department': department,
                    'email': email,
                    'register_time': datetime.now().isoformat(),
                    'embedding': embedding
                }
                self.save_database()
                print(f"✅ JSON 註冊成功: {person_id}")
                return True, f"成功註冊 {name}（ID: {person_id}）"
            
        except Exception as e:
            print(f"💥 註冊錯誤: {str(e)}")
            import traceback
            traceback.print_exc()
            return False, f"註冊失敗：{str(e)}"
    
    def identify_face(self, image, threshold=0.15):
        """識別人臉"""
        try:
            print(f"🔍 開始識別，閾值: {threshold}")
            
            # 轉換圖片格式
            cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            print(f"📷 圖片尺寸: {cv_image.shape}")
            
            # 人臉檢測
            faces = app.get(cv_image)
            print(f"👤 檢測到 {len(faces)} 張人臉")
            
            if len(faces) == 0:
                return None, "未檢測到人臉"
            
            results = []
            for i, face in enumerate(faces):
                print(f"\n🔍 處理第 {i+1} 張人臉")
                print(f"📦 人臉框: {face.bbox}")
                print(f"🧬 特徵向量長度: {len(face.normed_embedding)}")
                print(f"🧬 特徵向量範圍: [{face.normed_embedding.min():.3f}, {face.normed_embedding.max():.3f}]")
                
                if self.use_postgres:
                    # 使用 PostgreSQL 向量搜尋
                    print("🐘 使用 PostgreSQL 搜尋")
                    matches = self.db.find_similar_faces(face.normed_embedding, threshold)
                    print(f"🎯 找到 {len(matches)} 個匹配")
                    
                    if matches:
                        best_match = matches[0]  # 取得最佳匹配
                        print(f"✅ 最佳匹配: {best_match['name']} (信心度: {best_match['confidence']:.3f})")
                        
                        # 記錄單次識別事件
                        self.db.log_recognition(best_match['person_id'], best_match['name'], 
                                              best_match['confidence'], "gradio_upload")
                        
                        # 記錄或更新出勤會話
                        self.db.log_attendance(best_match['person_id'])

                        results.append({
                            'bbox': face.bbox,
                            'person_id': best_match['person_id'],
                            'name': best_match['name'],
                            'role': best_match['role'],
                            'department': best_match['department'],
                            'confidence': best_match['confidence']
                        })
                    else:
                        print("❌ 沒有找到匹配的人臉")
                        results.append({
                            'bbox': face.bbox,
                            'person_id': 'unknown',
                            'name': '',
                            'role': '',
                            'department': '',
                            'confidence': 0.0
                        })
                else:
                    # 使用 JSON 線性搜尋
                    print("📁 使用 JSON 搜尋")
                    print(f"📊 資料庫中有 {len(self.faces)} 個人臉")
                    
                    best_match = None
                    best_score = 0
                    
                    for person_id, info in self.faces.items():
                        # 計算相似度
                        similarity = np.dot(face.normed_embedding, info['embedding'])
                        print(f"🔍 與 {info['name']} 的相似度: {similarity:.3f}")
                        
                        if similarity > best_score:
                            best_score = similarity
                            best_match = (person_id, info)
                    
                    print(f"🏆 最高相似度: {best_score:.3f} (閾值: {threshold})")
                    
                    if best_score >= threshold:
                        print(f"✅ 識別成功: {best_match[1]['name']}")
                        results.append({
                            'bbox': face.bbox,
                            'person_id': best_match[0],
                            'name': best_match[1]['name'],
                            'role': best_match[1]['role'],
                            'department': best_match[1].get('department', ''),
                            'confidence': best_score
                        })
                    else:
                        print(f"❌ 相似度不足，識別為未知")
                        results.append({
                            'bbox': face.bbox,
                            'person_id': 'unknown',
                            'name': '',
                            'role': '',
                            'department': '',
                            'confidence': best_score
                        })
            
            print(f"🎯 識別完成，返回 {len(results)} 個結果")
            return results, "識別完成"
            
        except Exception as e:
            print(f"💥 識別錯誤: {str(e)}")
            import traceback
            traceback.print_exc()
            return None, f"識別失敗：{str(e)}"

# 初始化資料庫
face_db = FaceDatabase()

# 全域變數用於串流控制
streaming = False
stream_thread = None

def draw_face_boxes(image, results):
    """在圖片上繪製人臉框和標籤"""
    if not results:
        return image
    
    # 轉換為 OpenCV 格式
    cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
    
    for result in results:
        bbox = result['bbox'].astype(int)
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
        cv2.rectangle(cv_image, (x1, y1), (x2, y2), color, 2)
        
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
            cv2.rectangle(cv_image, (x1, y1-60), (x1 + max_width, y1), color, -1)
            
            # 畫文字
            cv2.putText(cv_image, role_text, (x1 + 5, y1 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            cv2.putText(cv_image, label, (x1 + 5, y1 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(cv_image, conf_text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    
    # 轉換回 PIL 格式
    return Image.fromarray(cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB))

def register_new_face(name, role, department, email, image):
    """註冊新人臉的 Gradio 函數"""
    if not name or not role:
        return "請填寫姓名和身分"
    
    if image is None:
        return "請上傳圖片"
    
    success, message = face_db.register_face(name, role, department, image, None, email)
    return message

def identify_faces(image):
    """識別人臉的 Gradio 函數"""
    if image is None:
        return None, "請上傳圖片"
    
    results, message = face_db.identify_face(image)
    
    if results:
        # 繪製標示框
        labeled_image = draw_face_boxes(image, results)
        
        # 統計已識別和未識別的人臉
        identified_faces = [r for r in results if r['person_id'] != 'unknown']
        unknown_faces = [r for r in results if r['person_id'] == 'unknown']
        
        # 生成結果文字
        result_text = f"檢測到 {len(results)} 張人臉"
        if unknown_faces:
            result_text += f"（{len(identified_faces)} 張已識別，{len(unknown_faces)} 張未識別）"
        result_text += "：\n\n"
        
        # 只顯示已識別的人臉詳情
        if identified_faces:
            result_text += "已識別人員：\n"
            for i, result in enumerate(identified_faces, 1):
                result_text += f"{i}. {result['name']} ({result['role']}) - 信心度: {result['confidence']:.3f}\n"
        
        if unknown_faces:
            result_text += f"\n未識別人臉：{len(unknown_faces)} 張（顯示為紅色框）"
        
        return labeled_image, result_text
    else:
        return image, message

def process_video(video_file):
    """處理影片的 Gradio 函數"""
    if video_file is None:
        return None, "請上傳影片檔案"
    
    try:
        # 創建臨時輸出檔案
        temp_output = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        temp_output.close()
        
        # 開啟影片
        cap = cv2.VideoCapture(video_file)
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # 設定影片寫入器
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(temp_output.name, fourcc, fps, (width, height))
        
        processed_frames = 0
        detected_faces = 0
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            # 轉換為 PIL 格式進行識別
            pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            results, _ = face_db.identify_face(pil_image)
            
            if results:
                detected_faces += len(results)
                # 在影片幀上繪製標示框
                for result in results:
                    bbox = result['bbox'].astype(int)
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
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    
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
                        cv2.rectangle(frame, (x1, y1-60), (x1 + max_width, y1), color, -1)
                        
                        # 畫文字
                        cv2.putText(frame, role_text, (x1 + 5, y1 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                        cv2.putText(frame, label, (x1 + 5, y1 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                        cv2.putText(frame, conf_text, (x1 + 5, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
            
            out.write(frame)
            processed_frames += 1
        
        cap.release()
        out.release()
        
        result_text = f"影片處理完成！\n"
        result_text += f"總幀數: {total_frames}\n"
        result_text += f"處理幀數: {processed_frames}\n"
        result_text += f"檢測到人臉: {detected_faces}\n"
        
        return temp_output.name, result_text
        
    except Exception as e:
        return None, f"影片處理錯誤: {str(e)}"

def start_webcam_stream():
    """啟動攝像頭串流"""
    return "攝像頭串流功能需要額外的權限設定，請使用影片上傳功能進行測試。"

def get_database_info():
    """取得資料庫資訊（舊版本，保持兼容）"""
    return get_database_stats()

def get_database_stats():
    """取得資料庫統計"""
    try:
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            
            # 用戶統計
            cursor.execute("SELECT COUNT(*) FROM face_profiles")
            total_users = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM face_profiles WHERE role = '員工'")
            employees = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM face_profiles WHERE role = '訪客'")
            visitors = cursor.fetchone()[0]
            
            # 識別統計
            cursor.execute("SELECT COUNT(*) FROM recognition_logs")
            total_recognitions = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM recognition_logs WHERE recognition_time > NOW() - INTERVAL '24 hours'")
            today_recognitions = cursor.fetchone()[0]
            
            cursor.close()
            
            stats = f"""📊 資料庫統計
            
👥 用戶統計：
• 總用戶數：{total_users}
• 員工：{employees}
• 訪客：{visitors}

🔍 識別統計：
• 總識別次數：{total_recognitions}
• 今日識別次數：{today_recognitions}
"""
            return stats
        else:
            total_faces = len(face_db.faces)
            employees = sum(1 for info in face_db.faces.values() if info['role'] == '員工')
            visitors = total_faces - employees
            
            return f"""📁 JSON 檔案資料庫
            
👥 用戶統計：
• 總用戶數：{total_faces}
• 員工：{employees}
• 訪客：{visitors}
"""
    except Exception as e:
        return f"統計錯誤: {str(e)}"

def get_all_users():
    """取得所有用戶"""
    try:
        if face_db.use_postgres:
            all_faces = face_db.db.get_all_faces()
            users = []
            for person_id, info in all_faces.items():
                users.append([
                    person_id,
                    info['name'],
                    info['role'],
                    info.get('department', ''),
                    info.get('email', ''),
                    info.get('register_time', '')
                ])
            return users
        else:
            users = []
            for person_id, info in face_db.faces.items():
                users.append([
                    person_id,
                    info['name'],
                    info['role'],
                    info.get('department', ''),
                    info.get('email', ''),
                    info.get('register_time', '')
                ])
            return users
    except Exception as e:
        return [["錯誤", str(e), "", "", "", ""]]

def update_user(person_id, name, role, department, email):
    """更新用戶資訊"""
    try:
        if not person_id:
            return "請選擇要更新的用戶"
        
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            cursor.execute("""
                UPDATE face_profiles 
                SET name = %s, role = %s, department = %s, email = %s, updated_at = NOW()
                WHERE person_id = %s
            """, (name, role, department, email, person_id))
            face_db.db.conn.commit()
            cursor.close()
            
            return f"用戶 {person_id} 更新成功"
        else:
            if person_id in face_db.faces:
                face_db.faces[person_id]['name'] = name
                face_db.faces[person_id]['role'] = role
                face_db.faces[person_id]['department'] = department
                face_db.faces[person_id]['email'] = email
                face_db.save_database()
                return f"用戶 {person_id} 更新成功"
            else:
                return "用戶不存在"
    except Exception as e:
        return f"更新失敗: {str(e)}"

def delete_user(person_id):
    """刪除用戶"""
    try:
        if not person_id:
            return "請選擇要刪除的用戶"
        
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            cursor.execute("DELETE FROM face_profiles WHERE person_id = %s", (person_id,))
            face_db.db.conn.commit()
            cursor.close()
            
            return f"用戶 {person_id} 刪除成功"
        else:
            if person_id in face_db.faces:
                del face_db.faces[person_id]
                face_db.save_faces()
                return f"用戶 {person_id} 刪除成功"
            else:
                return "用戶不存在"
    except Exception as e:
        return f"刪除失敗: {str(e)}"

def get_recognition_logs():
    """取得識別日誌"""
    try:
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            cursor.execute("""
                SELECT person_id, recognized_name, confidence, image_source, recognition_time
                FROM recognition_logs 
                ORDER BY recognition_time DESC 
                LIMIT 50
            """
            )
            logs = cursor.fetchall()
            cursor.close()
            
            result = []
            for log in logs:
                result.append([
                    log[0],  # person_id
                    log[1],  # recognized_name
                    f"{log[2]:.3f}",  # confidence
                    log[3],  # image_source
                    log[4].strftime("%Y-%m-%d %H:%M:%S")  # recognition_time
                ])
            return result
        else:
            return [["JSON模式", "不支援日誌", "", "", ""]]
    except Exception as e:
        return [["錯誤", str(e), "", "", ""]]

def get_attendance_logs():
    """取得出勤日誌"""
    try:
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            # Join with face_profiles to get the name
            cursor.execute("""
                SELECT 
                    p.name,
                    s.status,
                    s.arrival_time,
                    s.departure_time,
                    s.last_seen_at,
                    s.person_id
                FROM attendance_sessions s
                JOIN face_profiles p ON s.person_id = p.person_id
                ORDER BY s.last_seen_at DESC 
                LIMIT 100
            """
            )
            logs = cursor.fetchall()
            cursor.close()
            
            result = []
            taipei_tz = pytz.timezone('Asia/Taipei')
            
            for log in logs:
                name, status, arrival, departure, last_seen, person_id = log
                
                # 確保時間都有時區資訊
                if arrival and arrival.tzinfo is None:
                    arrival = taipei_tz.localize(arrival)
                if departure and departure.tzinfo is None:
                    departure = taipei_tz.localize(departure)
                
                # Format times
                arrival_str = arrival.strftime("%Y-%m-%d %H:%M:%S") if arrival else ""
                departure_str = departure.strftime("%Y-%m-%d %H:%M:%S") if departure else "在席中"
                
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
                    now = datetime.now(taipei_tz)
                    duration = now - arrival
                    total_seconds = int(duration.total_seconds())
                    hours, remainder = divmod(total_seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    if hours > 0:
                        duration_str = f"已持續 {hours}時{minutes}分"
                    else:
                        duration_str = f"已持續 {minutes}分鐘"

                result.append([
                    name,
                    "活躍" if status == 'active' else "結束",
                    arrival_str,
                    departure_str,
                    duration_str
                ])
            return result
        else:
            return [["JSON模式", "不支援", "", "", ""]]
    except Exception as e:
        print(f"取得出勤日誌錯誤: {e}")
        import traceback
        traceback.print_exc()
        return [["錯誤", str(e), "", "", ""]]

def clear_logs():
    """清除識別日誌"""
    try:
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            cursor.execute("DELETE FROM recognition_logs")
            face_db.db.conn.commit()
            cursor.close()
            return "日誌清除成功"
        else:
            return "JSON模式不支援日誌清除"
    except Exception as e:
        return f"清除失敗: {str(e)}"

def clear_attendance_logs():
    """清除出勤日誌"""
    try:
        if face_db.use_postgres:
            cursor = face_db.db.conn.cursor()
            cursor.execute("DELETE FROM attendance_sessions")
            face_db.db.conn.commit()
            cursor.close()
            return "出勤日誌清除成功"
        else:
            return "JSON模式不支援日誌清除"
    except Exception as e:
        return f"清除失敗: {str(e)}"

# 建立 Gradio 介面
with gr.Blocks(title="AuraFace 智能識別系統") as demo:
    gr.Markdown("# 🔍 AuraFace 智能識別系統")
    gr.Markdown("輕量級人臉建檔與識別系統 - 支援圖片、影片處理和實時串流")
    
    with gr.Tabs():
        # 人臉註冊頁面
        with gr.TabItem("👤 人臉註冊"):
            gr.Markdown("## 註冊新人臉")
            
            with gr.Row():
                with gr.Column():
                    reg_image = gr.Image(type="pil", label="上傳人臉圖片")
                    reg_name = gr.Textbox(label="姓名", placeholder="請輸入姓名")
                    reg_role = gr.Dropdown(
                        choices=["員工", "訪客"], 
                        label="身分", 
                        value="員工"
                    )
                    reg_dept = gr.Textbox(label="部門", placeholder="請輸入部門（可選）")
                    reg_email = gr.Textbox(label="電子信箱", placeholder="請輸入電子信箱（可選）")
                    reg_btn = gr.Button("註冊", variant="primary")
                
                with gr.Column():
                    reg_result = gr.Textbox(label="註冊結果", lines=3)
            
            reg_btn.click(
                register_new_face,
                inputs=[reg_name, reg_role, reg_dept, reg_email, reg_image],
                outputs=reg_result
            )
        
        # 人臉識別頁面
        with gr.TabItem("🔍 人臉識別"):
            gr.Markdown("## 識別人臉身分")
            
            with gr.Row():
                with gr.Column():
                    id_image = gr.Image(type="pil", label="上傳要識別的圖片")
                    id_btn = gr.Button("開始識別", variant="primary")
                
                with gr.Column():
                    id_result_image = gr.Image(label="識別結果（含標示框）")
                    id_result_text = gr.Textbox(label="識別詳情", lines=5)
            
            id_btn.click(
                identify_faces,
                inputs=id_image,
                outputs=[id_result_image, id_result_text]
            )
        
        # 資料庫管理頁面
        with gr.TabItem("📊 資料庫管理"):
            gr.Markdown("## 資料庫管理")
            
            with gr.Tabs():
                # 資料庫統計
                with gr.TabItem("📈 統計資訊"):
                    with gr.Row():
                        refresh_stats_btn = gr.Button("刷新統計", variant="secondary")
                    
                    db_stats = gr.Textbox(label="資料庫統計", lines=10, value=get_database_stats())
                    refresh_stats_btn.click(get_database_stats, outputs=db_stats)
                
                # 用戶管理
                with gr.TabItem("👥 用戶管理"):
                    with gr.Row():
                        with gr.Column():
                            refresh_users_btn = gr.Button("刷新用戶列表", variant="secondary")
                            user_table = gr.Dataframe(
                                headers=["用戶ID", "姓名", "身分", "部門", "電子信箱", "註冊時間"],
                                datatype=["str", "str", "str", "str", "str", "str"],
                                value=get_all_users(),
                                interactive=False
                            )
                        
                        with gr.Column():
                            gr.Markdown("### 編輯用戶")
                            edit_person_id = gr.Textbox(label="用戶ID", placeholder="點擊表格行選擇用戶")
                            edit_name = gr.Textbox(label="姓名")
                            edit_role = gr.Dropdown(choices=["員工", "訪客"], label="身分")
                            edit_department = gr.Textbox(label="部門")
                            edit_email = gr.Textbox(label="電子信箱")
                            
                            with gr.Row():
                                update_btn = gr.Button("更新用戶", variant="primary")
                                delete_btn = gr.Button("刪除用戶", variant="stop")
                            
                            user_operation_result = gr.Textbox(label="操作結果", lines=2)
                
                # 識別日誌
                with gr.TabItem("📋 識別日誌"):
                    with gr.Row():
                        refresh_logs_btn = gr.Button("刷新日誌", variant="secondary")
                        clear_logs_btn = gr.Button("清除日誌", variant="stop")
                    
                    log_table = gr.Dataframe(
                        headers=["用戶ID", "識別姓名", "信心度", "來源", "識別時間"],
                        datatype=["str", "str", "str", "str", "str"],
                        value=get_recognition_logs(),
                        interactive=False
                    )
                    
                    log_operation_result = gr.Textbox(label="操作結果", lines=2)

                # 出勤紀錄 (NEW TAB)
                with gr.TabItem("🕒 出勤紀錄"):
                    with gr.Row():
                        refresh_attendance_logs_btn = gr.Button("刷新出勤紀錄", variant="secondary")
                        clear_attendance_logs_btn = gr.Button("清除出勤紀錄", variant="stop")
                    
                    attendance_log_table = gr.Dataframe(
                        headers=["姓名", "狀態", "到達時間", "離開時間", "在席時長"],
                        datatype=["str", "str", "str", "str", "str"],
                        value=get_attendance_logs(),
                        interactive=False
                    )
                    
                    attendance_log_operation_result = gr.Textbox(label="操作結果", lines=2)
            
            # 事件綁定
            def select_user(evt: gr.SelectData):
                row = evt.index[0]
                users = get_all_users()
                if row < len(users):
                    user = users[row]
                    return user[0], user[1], user[2], user[3], user[4]
                return "", "", "", "", ""
            
            user_table.select(select_user, outputs=[edit_person_id, edit_name, edit_role, edit_department, edit_email])
            
            refresh_users_btn.click(get_all_users, outputs=user_table)
            update_btn.click(
                update_user,
                inputs=[edit_person_id, edit_name, edit_role, edit_department, edit_email],
                outputs=user_operation_result
            ).then(get_all_users, outputs=user_table)
            delete_btn.click(
                delete_user,
                inputs=edit_person_id,
                outputs=user_operation_result
            ).then(get_all_users, outputs=user_table)
            
            refresh_logs_btn.click(get_recognition_logs, outputs=log_table)
            clear_logs_btn.click(clear_logs, outputs=log_operation_result).then(get_recognition_logs, outputs=log_table)

            # New bindings for attendance logs
            refresh_attendance_logs_btn.click(get_attendance_logs, outputs=attendance_log_table)
            clear_attendance_logs_btn.click(clear_attendance_logs, outputs=attendance_log_operation_result).then(get_attendance_logs, outputs=attendance_log_table)

        # 影片處理頁面
        with gr.TabItem("🎬 影片處理"):
            gr.Markdown("## 影片人臉識別")
            gr.Markdown("上傳影片進行批次人臉識別和標示")
            
            with gr.Row():
                with gr.Column():
                    video_input = gr.Video(label="上傳影片檔案")
                    video_btn = gr.Button("開始處理影片", variant="primary")
                
                with gr.Column():
                    video_output = gr.Video(label="處理後影片")
                    video_result = gr.Textbox(label="處理結果", lines=5)
            
            video_btn.click(
                process_video,
                inputs=video_input,
                outputs=[video_output, video_result]
            )
        
        # 串流識別頁面
        with gr.TabItem("📹 即時識別"):
            gr.Markdown("## WebSocket 即時識別")
            gr.Markdown("高效能攝像頭即時人臉識別系統")
            
            with gr.Row():
                with gr.Column():
                    gr.Markdown("""
                    ### 🚀 啟動 WebSocket 伺服器
                    ```bash
                    # 在容器中執行
                    docker exec -it auraface-app python websocket_realtime.py
                    
                    # 或直接執行
                    python websocket_realtime.py
                    ```
                    """
                    )
                    
                    stream_info = gr.Textbox(
                        label="伺服器狀態", 
                        value="WebSocket 伺服器位址: ws://localhost:7861", 
                        lines=3
                    )
                
                with gr.Column():
                    gr.Markdown("""
                    ### 📱 客戶端連接
                    
                    **方案1: 瀏覽器攝像頭**
                    - 開啟 `realtime_client.html`
                    - 允許攝像頭權限
                    - 即時識別和註冊
                    
                    **方案2: IP 攝像頭**
                    - 支援 RTSP/HTTP 串流
                    - 多攝像頭同時識別
                    - 適合商用部署
                    
                    **方案3: 手機攝像頭**
                    - 使用 IP Webcam APP
                    - 零成本靈活部署
                    - 高畫質識別
                    
                    **效能**: 10-30ms 延遲，支援 10+ FPS
                    """
                    )
            
            gr.Button("開啟測試頁面", variant="primary", link="realtime_client.html")

        # API 接口頁面  
        with gr.TabItem("🔌 API 接口"):
            gr.Markdown("## 出勤記錄 API")
            gr.Markdown("提供 JSON 格式的出勤數據查詢接口")
            
            with gr.Row():
                with gr.Column():
                    gr.Markdown("""
                    ### 📋 可用端點
                    
                    **查詢所有出勤記錄**
                    ```
                    GET http://localhost:7859/api/attendance?limit=10
                    ```
                    
                    **按姓名查詢**
                    ```
                    GET http://localhost:7859/api/attendance?name=CSL&limit=5
                    ```
                    
                    **按人員ID查詢**
                    ```
                    GET http://localhost:7859/api/attendance?person_id=員工_0001_xxx
                    ```
                    
                    **健康檢查**
                    ```
                    GET http://localhost:7859/api/health
                    ```
                    
                    **API 文檔**
                    ```
                    GET http://localhost:7859/docs
                    ```
                    """)
                
                with gr.Column():
                    api_test_name = gr.Textbox(label="測試姓名查詢", placeholder="輸入姓名")
                    api_test_limit = gr.Number(label="查詢數量", value=5, minimum=1, maximum=50)
                    api_test_btn = gr.Button("測試 API", variant="primary")
                    api_result = gr.JSON(label="API 回應結果", value={})
            
            def test_api(name, limit):
                from api.attendance_api import get_attendance_data_json
                return get_attendance_data_json(name=name if name else None, limit=int(limit))
            
            api_test_btn.click(
                test_api,
                inputs=[api_test_name, api_test_limit],
                outputs=api_result
            )

# --- 背景任務：定期結束超時的會話 ---
def session_cleanup_task():
    """定期檢查並結束超時的會話"""
    while True:
        print("背景任務：正在檢查超時會話...")
        try:
            if face_db.use_postgres:
                # 使用 face_db.db 存取 PostgresFaceDatabase 實例
                face_db.db.end_timed_out_sessions(timeout_seconds=300) # 5分鐘寬限期
        except Exception as e:
            print(f"背景任務出錯: {e}")
        time.sleep(60) # 每60秒檢查一次

# 啟動背景 FastAPI 服務
def start_background_api():
    """在背景執行緒中啟動 FastAPI 服務"""
    try:
        import subprocess
        import sys
        # 在背景啟動 FastAPI 服務
        api_thread = threading.Thread(
            target=lambda: subprocess.run([sys.executable, "api/standalone_api.py"]), 
            daemon=True
        )
        api_thread.start()
        print("✅ 背景 FastAPI 服務已啟動在端口 7859")
    except Exception as e:
        print(f"❌ 背景 FastAPI 服務啟動失敗: {e}")

if __name__ == "__main__":
    # 啟動背景任務執行緒
    # 只有在使用 PostgreSQL 時才啟動背景任務
    if face_db.use_postgres:
        cleanup_thread = threading.Thread(target=session_cleanup_task, daemon=True)
        cleanup_thread.start()
        print("✅ 已啟動背景會話清理任務。")

    # 啟動背景 API 服務
    start_background_api()

    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False
    )
