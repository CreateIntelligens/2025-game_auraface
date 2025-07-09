-- 確保資料庫存在並連接
-- 注意：POSTGRES_DB 環境變數會自動創建資料庫，這裡只是確保

-- 確保用戶存在 (如果不存在則創建)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ai360') THEN
        CREATE USER ai360 WITH PASSWORD 'ai360';
    END IF;
END
$$;

-- 給予用戶權限
GRANT ALL PRIVILEGES ON DATABASE auraface TO ai360;

-- 創建 pgvector 擴展
CREATE EXTENSION IF NOT EXISTS vector;

-- 創建人臉資料表
CREATE TABLE IF NOT EXISTS face_profiles (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(50) UNIQUE NOT NULL,
    employee_id VARCHAR(50),  -- 員工編號
    name VARCHAR(100) NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('員工', '訪客')),
    department VARCHAR(100),
    face_embedding VECTOR(512),
    register_time TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 創建向量相似度索引 (HNSW - 高效能近似最近鄰搜尋)
CREATE INDEX IF NOT EXISTS face_embedding_idx 
ON face_profiles USING hnsw (face_embedding vector_cosine_ops);

-- 創建識別記錄表
CREATE TABLE IF NOT EXISTS recognition_logs (
    id SERIAL PRIMARY KEY,
    person_id VARCHAR(50),
    recognized_name VARCHAR(100),
    confidence FLOAT,
    recognition_time TIMESTAMP DEFAULT NOW(),
    image_source VARCHAR(100)
);

-- 創建更新時間觸發器
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_face_profiles_updated_at 
BEFORE UPDATE ON face_profiles 
FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

-- 確保 ai360 用戶可以訪問所有表
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ai360;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ai360;
GRANT USAGE ON SCHEMA public TO ai360;

-- 設定未來創建的表也給予權限
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ai360;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ai360;
