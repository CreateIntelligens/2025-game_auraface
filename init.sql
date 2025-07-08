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
