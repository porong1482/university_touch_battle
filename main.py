from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
import aiosqlite
import os

app = FastAPI()
from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="static"), name="static")

SECRET_KEY = os.environ.get("SECRET_KEY", "supersecretkey1234")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7일

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
DB_PATH = os.environ.get("DB_PATH", "touches.db")


# ── DB 초기화 ──────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                university TEXT DEFAULT '',
                items TEXT DEFAULT '',
                titles TEXT DEFAULT '',
                touch_count INTEGER DEFAULT 0,
                slots TEXT DEFAULT '',
                visited_unis TEXT DEFAULT '',
                uni_counts TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS touches (
                university TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        # 기존 DB에 새 컬럼 추가
        for col in ["slots", "visited_unis", "uni_counts"]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT DEFAULT ''")
            except:
                pass
        await db.commit()


@app.on_event("startup")
async def startup():
    await init_db()


# ── 유틸 ───────────────────────────────────────────
def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="인증 실패")
    except JWTError:
        raise HTTPException(status_code=401, detail="인증 실패")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = await cursor.fetchone()
    if user is None:
        raise HTTPException(status_code=401, detail="유저 없음")
    return {
        "id": user[0],
        "username": user[1],
        "university": user[3],
        "items": user[4],
        "titles": user[5],
        "touch_count": user[6],
        "slots": user[7],
        "visited_unis": user[8],
        "uni_counts": user[9],
    }


# ── 모델 ───────────────────────────────────────────
class RegisterData(BaseModel):
    username: str
    password: str


class TouchData(BaseModel):
    university: str
    point: int = 1


class UpdateData(BaseModel):
    items: str = ""
    titles: str = ""
    university: str = ""
    slots: str = ""
    visited_unis: str = ""
    uni_counts: str = ""


# ── 라우터 ─────────────────────────────────────────
@app.get("/")
def read_root():
    return FileResponse("index.html")


@app.post("/register")
async def register(data: RegisterData):
    hashed = get_password_hash(data.password)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)",
                (data.username, hashed),
            )
            await db.commit()
    except Exception:
        raise HTTPException(status_code=400, detail="이미 존재하는 닉네임이에요!")
    token = create_access_token({"sub": data.username})
    return {"access_token": token, "token_type": "bearer", "username": data.username}


@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM users WHERE username = ?", (form_data.username,)
        )
        user = await cursor.fetchone()
    if not user or not verify_password(form_data.password, user[2]):
        raise HTTPException(status_code=400, detail="닉네임 또는 비밀번호가 틀렸어요!")
    token = create_access_token({"sub": form_data.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": form_data.username,
    }


@app.get("/me")
async def me(current_user=Depends(get_current_user)):
    return current_user


@app.post("/touch")
async def touch(data: TouchData, current_user=Depends(get_current_user)):
    if data.point < 1 or data.point > 5:
        raise HTTPException(status_code=400, detail="잘못된 요청")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO touches (university, count) VALUES (?, ?)
            ON CONFLICT(university) DO UPDATE SET count = count + ?
        """,
            (data.university, data.point, data.point),
        )
        await db.execute(
            """
            UPDATE users SET touch_count = touch_count + 1, university = ?
            WHERE username = ?
        """,
            (data.university, current_user["username"]),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT count FROM touches WHERE university = ?", (data.university,)
        )
        row = await cursor.fetchone()
        cursor2 = await db.execute(
            "SELECT touch_count FROM users WHERE username = ?",
            (current_user["username"],),
        )
        row2 = await cursor2.fetchone()
    return {"university": data.university, "count": row[0], "my_count": row2[0]}


@app.post("/update_user")
async def update_user(data: UpdateData, current_user=Depends(get_current_user)):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE users SET items = ?, titles = ?, slots = ?, visited_unis = ?, uni_counts = ?
            WHERE username = ?
        """,
            (
                data.items,
                data.titles,
                data.slots,
                data.visited_unis,
                data.uni_counts,
                current_user["username"],
            ),
        )
        await db.commit()
    return {"ok": True}


@app.get("/ranking")
async def ranking():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT university, count FROM touches ORDER BY count DESC"
        )
        rows = await cursor.fetchall()
    return {"ranking": [{"university": r[0], "count": r[1]} for r in rows]}
