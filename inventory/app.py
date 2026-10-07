from __future__ import annotations

import sqlite3
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, session, url_for

from inventory_agent import InventoryAgent


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "inventory.db"

ADMIN_PASSWORD = "admin1004"
DEFAULT_CATEGORIES = ("케이블", "공구", "소모품", "기타")
PER_PAGE = 10

app = Flask(__name__)
app.secret_key = "small-warehouse-secret-key"
inventory_agent = InventoryAgent(DB_PATH)


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error: Exception | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registered_date TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL UNIQUE,
            minimum_stock INTEGER NOT NULL DEFAULT 0 CHECK(minimum_stock >= 0),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            movement_date TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL CHECK(movement_type IN ('입고', '출고')),
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
        """
    )
    for category in DEFAULT_CATEGORIES:
        db.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (category,))
    db.commit()


def paginate(total_count: int, page: int, per_page: int = PER_PAGE) -> dict[str, int]:
    total_pages = max(1, ceil(total_count / per_page)) if total_count else 1
    current_page = max(1, min(page, total_pages))
    offset = (current_page - 1) * per_page
    return {"page": current_page, "pages": total_pages, "offset": offset, "per_page": per_page}


def get_categories() -> list[sqlite3.Row]:
    return get_db().execute("SELECT id, name FROM categories ORDER BY name").fetchall()


def find_product(product_id: int) -> sqlite3.Row | None:
    return get_db().execute(
        """
        SELECT p.id, p.registered_date, p.name, p.minimum_stock, c.id AS category_id, c.name AS category_name
        FROM products p
        JOIN categories c ON c.id = p.category_id
        WHERE p.id = ?
        """,
        (product_id,),
    ).fetchone()


def get_stock_map() -> dict[int, int]:
    rows = get_db().execute(
        """
        SELECT
            p.id AS product_id,
            COALESCE(SUM(CASE WHEN m.movement_type = '입고' THEN m.quantity ELSE -m.quantity END), 0) AS stock
        FROM products p
        LEFT JOIN movements m ON m.product_id = p.id
        GROUP BY p.id
        """
    ).fetchall()
    return {row["product_id"]: int(row["stock"]) for row in rows}


def require_admin() -> None:
    if not session.get("is_admin"):
        abort(403)


BASE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title }} - 소형창고 재고관리</title>
  <style>
    :root {
      --bg: #f4fbff;
      --sidebar: #f3fff5;
      --card: #ffffff;
      --text: #243238;
      --muted: #65838e;
      --green: #dff7e4;
      --blue: #dceeff;
      --orange: #ffe9d6;
      --border: #d4e4ec;
      --accent: #2c7bb5;
      --ok: #1f8b56;
      --warn: #d9740f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Noto Sans KR", sans-serif;
      background: linear-gradient(140deg, #f4fff6, #f3faff, #fff8ef);
      color: var(--text);
    }
    .layout {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 20% 80%;
    }
    .sidebar {
      border-right: 1px solid var(--border);
      background: var(--sidebar);
      padding: 24px 20px;
    }
    .brand { font-size: 20px; font-weight: 700; margin-bottom: 14px; }
    .sub { font-size: 12px; color: var(--muted); margin-bottom: 20px; }
    .group-title {
      margin-top: 18px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }
    .nav a {
      display: block;
      margin-bottom: 8px;
      text-decoration: none;
      color: var(--text);
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid transparent;
    }
    .nav a.active { background: #e7f8ec; border-color: #caedd6; }
    .nav a:hover { border-color: var(--border); background: #f8fdff; }
    .admin {
      margin-top: 20px;
      padding-top: 12px;
      border-top: 1px solid var(--border);
      font-size: 14px;
    }
    .main {
      padding: 24px;
    }
    .head-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
    }
    .head-row h1 {
      margin: 0;
      font-size: 36px;
      letter-spacing: -1px;
    }
    .badge {
      display: inline-block;
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 11px;
      font-size: 12px;
      color: var(--muted);
    }
    .flash {
      margin-bottom: 12px;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid #cae4f5;
      background: #edf7ff;
      font-size: 14px;
    }
    .cards {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-bottom: 14px;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 8px 20px rgba(55, 84, 126, 0.04);
    }
    .card h3 { margin: 0 0 10px; font-size: 16px; }
    .bg-green { background: linear-gradient(0deg, #fff, #f6fff8); }
    .bg-blue { background: linear-gradient(0deg, #fff, #f5f9ff); }
    .bg-orange { background: linear-gradient(0deg, #fff, #fffaf5); }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      text-align: left;
      padding: 9px 8px;
      border-bottom: 1px solid #edf2f5;
      white-space: nowrap;
    }
    th { color: var(--muted); font-size: 12px; }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(180px, 1fr));
      gap: 10px;
    }
    .field { display: flex; flex-direction: column; gap: 6px; }
    label { font-size: 12px; color: var(--muted); }
    input, select, button {
      font: inherit;
      padding: 9px 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
    }
    button {
      cursor: pointer;
      background: var(--accent);
      color: #fff;
      border-color: #2c7bb5;
      font-weight: 600;
    }
    .btn-sub { background: #fff; color: var(--text); }
    .line { margin-top: 12px; display: flex; gap: 8px; align-items: center; }
    .danger { color: #c2462b; }
    .ok { color: var(--ok); }
    .pill {
      display: inline-block;
      border-radius: 999px;
      font-size: 12px;
      padding: 2px 8px;
      background: #eef5fa;
      color: #3f6786;
    }
    .pager {
      margin-top: 12px;
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: center;
    }
    .muted { color: var(--muted); }
    .search-box { display: flex; gap: 8px; margin-bottom: 12px; }
    .search-box input { flex: 1; }
    .chat-shell {
      height: calc(100vh - 145px);
      min-height: 520px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
      padding: 0;
    }
    .chat-guide {
      padding: 16px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(90deg, #f5fff7, #f4f9ff);
    }
    .chat-guide p { margin: 6px 0 10px; color: var(--muted); }
    .chat-examples { display: flex; flex-wrap: wrap; gap: 7px; }
    .chat-example {
      padding: 6px 9px;
      color: #3f6786;
      background: #fff;
      border-color: var(--border);
      font-size: 12px;
      font-weight: 500;
    }
    .chat-messages {
      overflow-y: auto;
      padding: 18px;
      background: #fbfdfe;
    }
    .chat-message {
      display: flex;
      margin-bottom: 14px;
    }
    .chat-message.user { justify-content: flex-end; }
    .chat-bubble {
      max-width: min(760px, 86%);
      padding: 11px 13px;
      border-radius: 14px;
      line-height: 1.55;
      overflow-wrap: anywhere;
    }
    .chat-message.agent .chat-bubble {
      background: #fff;
      border: 1px solid var(--border);
      border-top-left-radius: 4px;
    }
    .chat-message.user .chat-bubble {
      background: #dceeff;
      border: 1px solid #c4dff5;
      border-top-right-radius: 4px;
    }
    .chat-bubble p { margin: 0 0 7px; }
    .chat-bubble p:last-child { margin-bottom: 0; }
    .chat-bubble code {
      padding: 2px 5px;
      border-radius: 5px;
      background: #f0f4f6;
      font-family: Consolas, monospace;
    }
    .chat-table-wrap { overflow-x: auto; margin-top: 8px; }
    .chat-input {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 9px;
      padding: 14px;
      border-top: 1px solid var(--border);
      background: #fff;
    }
    .chat-input input { min-width: 0; }
    .chat-input button:disabled { opacity: .55; cursor: wait; }
    @media (max-width: 1000px) {
      .layout { grid-template-columns: 1fr; }
      .cards { grid-template-columns: 1fr; }
      .form-grid { grid-template-columns: 1fr; }
      .chat-shell { height: 70vh; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">stockroom</div>
      <div class="sub">SMALL WAREHOUSE</div>

      <div class="group-title">등록</div>
      <div class="nav">
        <a href="{{ url_for('product_register') }}" class="{{ 'active' if active == 'product' else '' }}">품목 등록</a>
        <a href="{{ url_for('movement_register') }}" class="{{ 'active' if active == 'movement' else '' }}">입출고 등록</a>
      </div>

      <div class="group-title">조회</div>
      <div class="nav">
        <a href="{{ url_for('inventory_status') }}" class="{{ 'active' if active == 'inventory' else '' }}">재고 현황</a>
        <a href="{{ url_for('product_history') }}" class="{{ 'active' if active == 'history' else '' }}">품목별 입출고 현황</a>
      </div>

      <div class="group-title">AI 도우미</div>
      <div class="nav">
        <a href="{{ url_for('chat') }}" class="{{ 'active' if active == 'chat' else '' }}">대화창</a>
      </div>

      <div class="admin">
        {% if session.get('is_admin') %}
          <div class="ok">관리자 로그인됨</div>
          <a href="{{ url_for('admin_manage') }}">관리자 설정</a><br />
          <a href="{{ url_for('admin_logout') }}">로그아웃</a>
        {% else %}
          <a href="{{ url_for('admin_login') }}">관리자 로그인</a>
        {% endif %}
      </div>
    </aside>
    <main class="main">
      <div class="head-row">
        <h1>{{ page_title }}</h1>
        <span class="badge">{{ today }}</span>
      </div>
      {% if message %}
      <div class="flash">{{ message }}</div>
      {% endif %}
      {{ content|safe }}
    </main>
  </div>
</body>
</html>
"""


def render_page(
    *,
    title: str,
    page_title: str,
    active: str,
    content: str,
    message: str = "",
    **context: Any,
) -> str:
    return render_template_string(
        BASE_TEMPLATE,
        title=title,
        page_title=page_title,
        active=active,
        content=render_template_string(content, **context),
        today=today_iso(),
        message=message,
        session=session,
        url_for=url_for,
    )


@app.before_request
def before_request() -> None:
    init_db()


@app.route("/")
def dashboard() -> str:
    db = get_db()
    low_stock = db.execute(
        """
        SELECT
            p.registered_date, c.name AS category_name, p.name AS product_name, p.minimum_stock,
            COALESCE(SUM(CASE WHEN m.movement_type = '입고' THEN m.quantity ELSE -m.quantity END), 0) AS current_stock
        FROM products p
        JOIN categories c ON c.id = p.category_id
        LEFT JOIN movements m ON m.product_id = p.id
        GROUP BY p.id
        HAVING current_stock <= p.minimum_stock
        ORDER BY current_stock ASC, p.name ASC
        LIMIT 5
        """
    ).fetchall()

    recent_movements = db.execute(
        """
        SELECT
            m.id, c.name AS category_name, p.name AS product_name, m.movement_type, m.quantity
        FROM movements m
        JOIN products p ON p.id = m.product_id
        JOIN categories c ON c.id = p.category_id
        ORDER BY m.id DESC
        LIMIT 5
        """
    ).fetchall()

    content = """
    <div class="cards">
      <section class="card bg-green">
        <h3>재고 부족 품목 5건</h3>
        <table>
          <thead><tr><th>등록일자</th><th>분류</th><th>제품명</th><th>최소재고</th><th>현재고</th></tr></thead>
          <tbody>
          {% for row in low_stock %}
            <tr>
              <td>{{ row['registered_date'] }}</td>
              <td>{{ row['category_name'] }}</td>
              <td>{{ row['product_name'] }}</td>
              <td>{{ row['minimum_stock'] }}</td>
              <td class="{{ 'danger' if row['current_stock'] <= row['minimum_stock'] else '' }}">{{ row['current_stock'] }}</td>
            </tr>
          {% else %}
            <tr><td colspan="5" class="muted">재고 부족 품목이 없습니다.</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </section>
      <section class="card bg-blue">
        <h3>최근 입출고 5건</h3>
        <table>
          <thead><tr><th>순번</th><th>분류</th><th>제품명</th><th>입출고</th><th>수량</th></tr></thead>
          <tbody>
          {% for row in recent_movements %}
            <tr>
              <td>{{ row['id'] }}</td>
              <td>{{ row['category_name'] }}</td>
              <td>{{ row['product_name'] }}</td>
              <td><span class="pill">{{ row['movement_type'] }}</span></td>
              <td>{{ row['quantity'] }}</td>
            </tr>
          {% else %}
            <tr><td colspan="5" class="muted">입출고 내역이 없습니다.</td></tr>
          {% endfor %}
          </tbody>
        </table>
      </section>
    </div>
    """

    return render_page(
        title="재고 관리",
        page_title="재고 관리",
        active="dashboard",
        content=content,
        low_stock=low_stock,
        recent_movements=recent_movements,
    )


@app.route("/products/register", methods=["GET", "POST"])
def product_register() -> str:
    db = get_db()
    message = ""
    if request.method == "POST":
        registered_date = request.form.get("registered_date", today_iso()).strip()
        category_id = request.form.get("category_id", "").strip()
        name = request.form.get("name", "").strip()
        initial_qty = int(request.form.get("initial_qty", "0"))
        minimum_stock = int(request.form.get("minimum_stock", "0"))

        if not (category_id and name):
            message = "분류와 제품명을 입력해 주세요."
        elif initial_qty < 0 or minimum_stock < 0:
            message = "수량은 0 이상이어야 합니다."
        else:
            try:
                cur = db.execute(
                    """
                    INSERT INTO products(registered_date, category_id, name, minimum_stock)
                    VALUES (?, ?, ?, ?)
                    """,
                    (registered_date, int(category_id), name, minimum_stock),
                )
                product_id = cur.lastrowid
                if initial_qty > 0:
                    db.execute(
                        """
                        INSERT INTO movements(movement_date, product_id, movement_type, quantity)
                        VALUES (?, ?, '입고', ?)
                        """,
                        (registered_date, product_id, initial_qty),
                    )
                db.commit()
                return redirect(url_for("product_register", ok=1))
            except sqlite3.IntegrityError:
                message = "이미 등록된 제품명입니다."

    if request.args.get("ok"):
        message = "품목 등록이 완료되었습니다."

    products = db.execute(
        """
        SELECT p.id, p.registered_date, p.name, p.minimum_stock, c.name AS category_name
        FROM products p
        JOIN categories c ON c.id = p.category_id
        ORDER BY p.id DESC
        LIMIT 10
        """
    ).fetchall()
    stocks = get_stock_map()

    content = """
    <section class="card bg-green">
      <h3>품목 등록</h3>
      <form method="post">
        <div class="form-grid">
          <div class="field">
            <label>등록일자</label>
            <input type="date" name="registered_date" value="{{ today }}" required />
          </div>
          <div class="field">
            <label>분류</label>
            <select name="category_id" required>
              <option value="">선택</option>
              {% for c in categories %}
                <option value="{{ c['id'] }}">{{ c['name'] }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="field">
            <label>제품명</label>
            <input type="text" name="name" placeholder="제품명" required />
          </div>
          <div class="field">
            <label>초기수량</label>
            <input type="number" min="0" name="initial_qty" value="0" required />
          </div>
          <div class="field">
            <label>최소 재고량</label>
            <input type="number" min="0" name="minimum_stock" value="0" required />
          </div>
        </div>
        <div class="line"><button type="submit">등록</button></div>
      </form>
    </section>

    <section class="card bg-blue" style="margin-top:14px;">
      <h3>최근 등록 품목</h3>
      <table>
        <thead><tr><th>순번</th><th>등록일자</th><th>분류</th><th>제품명</th><th>재고량</th><th>최소재고</th></tr></thead>
        <tbody>
        {% for p in products %}
          <tr>
            <td>{{ p['id'] }}</td>
            <td>{{ p['registered_date'] }}</td>
            <td>{{ p['category_name'] }}</td>
            <td>{{ p['name'] }}</td>
            <td>{{ stocks.get(p['id'], 0) }}</td>
            <td>{{ p['minimum_stock'] }}</td>
          </tr>
        {% else %}
          <tr><td colspan="6" class="muted">등록된 품목이 없습니다.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </section>
    """

    return render_page(
        title="품목 등록",
        page_title="품목 등록",
        active="product",
        content=content,
        categories=get_categories(),
        products=products,
        stocks=stocks,
        today=today_iso(),
        message=message,
    )


@app.route("/movements/register", methods=["GET", "POST"])
def movement_register() -> str:
    db = get_db()
    message = ""
    q = request.args.get("q", "").strip()
    page = int(request.args.get("page", "1"))
    selected_product_id = int(request.args.get("product_id", "0") or "0")

    if request.method == "POST":
        selected_product_id = int(request.form.get("product_id", "0"))
        movement_date = request.form.get("movement_date", today_iso()).strip()
        movement_type = request.form.get("movement_type", "").strip()
        quantity = int(request.form.get("quantity", "0"))
        product = find_product(selected_product_id)
        if not product:
            message = "제품을 선택해 주세요."
        elif movement_type not in ("입고", "출고"):
            message = "입출고 유형이 올바르지 않습니다."
        elif quantity <= 0:
            message = "수량은 1 이상이어야 합니다."
        else:
            current_stock = get_stock_map().get(selected_product_id, 0)
            if movement_type == "출고" and quantity > current_stock:
                message = f"현재고({current_stock})보다 많이 출고할 수 없습니다."
            else:
                db.execute(
                    """
                    INSERT INTO movements(movement_date, product_id, movement_type, quantity)
                    VALUES (?, ?, ?, ?)
                    """,
                    (movement_date, selected_product_id, movement_type, quantity),
                )
                db.commit()
                return redirect(
                    url_for("movement_register", q=q, page=page, product_id=selected_product_id, ok=1)
                )

    if request.args.get("ok"):
        message = "입출고 등록이 완료되었습니다."

    where_sql = ""
    args: tuple[Any, ...] = ()
    if q:
        where_sql = "WHERE p.name LIKE ?"
        args = (f"%{q}%",)

    count = db.execute(f"SELECT COUNT(*) FROM products p {where_sql}", args).fetchone()[0]
    pg = paginate(count, page)
    products = db.execute(
        f"""
        SELECT p.id, p.registered_date, p.name, p.minimum_stock, c.name AS category_name
        FROM products p
        JOIN categories c ON c.id = p.category_id
        {where_sql}
        ORDER BY p.id ASC
        LIMIT ? OFFSET ?
        """,
        args + (pg["per_page"], pg["offset"]),
    ).fetchall()
    selected = find_product(selected_product_id) if selected_product_id else None
    stocks = get_stock_map()

    content = """
    <section class="card bg-blue">
      <h3>제품 검색</h3>
      <form class="search-box" method="get">
        <input type="text" name="q" value="{{ q }}" placeholder="제품명을 검색하세요" />
        <button type="submit">검색</button>
      </form>
      <table>
        <thead><tr><th>순번</th><th>등록일자</th><th>분류</th><th>제품명</th><th>현재고</th><th>선택</th></tr></thead>
        <tbody>
        {% for p in products %}
          <tr>
            <td>{{ p['id'] }}</td>
            <td>{{ p['registered_date'] }}</td>
            <td>{{ p['category_name'] }}</td>
            <td>{{ p['name'] }}</td>
            <td>{{ stocks.get(p['id'], 0) }}</td>
            <td><a href="{{ url_for('movement_register', q=q, page=page, product_id=p['id']) }}">선택</a></td>
          </tr>
        {% else %}
          <tr><td colspan="6" class="muted">조회 결과가 없습니다.</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <div class="pager">
        {% if page > 1 %}
          <a href="{{ url_for('movement_register', q=q, page=page-1, product_id=selected_id) }}">이전</a>
        {% endif %}
        <span>{{ page }} / {{ pages }}</span>
        {% if page < pages %}
          <a href="{{ url_for('movement_register', q=q, page=page+1, product_id=selected_id) }}">다음</a>
        {% endif %}
      </div>
    </section>

    <section class="card bg-orange" style="margin-top:14px;">
      <h3>입출고 등록</h3>
      {% if selected %}
        <form method="post">
          <input type="hidden" name="product_id" value="{{ selected['id'] }}" />
          <div class="form-grid">
            <div class="field">
              <label>등록일자</label>
              <input type="date" name="movement_date" value="{{ today }}" required />
            </div>
            <div class="field">
              <label>분류</label>
              <input type="text" value="{{ selected['category_name'] }}" readonly />
            </div>
            <div class="field">
              <label>제품명</label>
              <input type="text" value="{{ selected['name'] }}" readonly />
            </div>
            <div class="field">
              <label>입출고</label>
              <select name="movement_type" required>
                <option value="입고">입고</option>
                <option value="출고">출고</option>
              </select>
            </div>
            <div class="field">
              <label>수량</label>
              <input type="number" min="1" name="quantity" required />
            </div>
            <div class="field">
              <label>현재고</label>
              <input type="text" value="{{ stocks.get(selected['id'], 0) }}" readonly />
            </div>
          </div>
          <div class="line"><button type="submit">입출고 등록</button></div>
        </form>
      {% else %}
        <p class="muted">상단 리스트에서 제품을 선택해 주세요.</p>
      {% endif %}
    </section>
    """

    return render_page(
        title="입출고 등록",
        page_title="입출고 등록",
        active="movement",
        content=content,
        q=q,
        page=pg["page"],
        pages=pg["pages"],
        products=products,
        selected=selected,
        selected_id=selected_product_id,
        stocks=stocks,
        today=today_iso(),
        message=message,
    )


@app.route("/inventory")
def inventory_status() -> str:
    db = get_db()
    page = int(request.args.get("page", "1"))
    rows = db.execute(
        """
        SELECT
            p.id, p.name, p.registered_date, p.minimum_stock, c.name AS category_name,
            MAX(m.movement_date) AS recent_movement_date,
            COALESCE(SUM(CASE WHEN m.movement_type = '입고' THEN m.quantity ELSE -m.quantity END), 0) AS stock
        FROM products p
        JOIN categories c ON c.id = p.category_id
        LEFT JOIN movements m ON m.product_id = p.id
        GROUP BY p.id
        ORDER BY stock ASC, p.id ASC
        """
    ).fetchall()

    total = len(rows)
    pg = paginate(total, page)
    sliced = rows[pg["offset"] : pg["offset"] + pg["per_page"]]

    content = """
    <section class="card bg-blue">
      <h3>재고 현황 (재고량 적은 순)</h3>
      <table>
        <thead><tr><th>순번</th><th>최근 입출고 일자</th><th>분류</th><th>제품명</th><th>재고량</th><th>최소 재고량</th><th>상태</th></tr></thead>
        <tbody>
        {% for r in rows %}
          <tr>
            <td>{{ r['id'] }}</td>
            <td>{{ r['recent_movement_date'] or '-' }}</td>
            <td>{{ r['category_name'] }}</td>
            <td>{{ r['name'] }}</td>
            <td class="{{ 'danger' if r['stock'] <= r['minimum_stock'] else '' }}">{{ r['stock'] }}</td>
            <td>{{ r['minimum_stock'] }}</td>
            <td>
              {% if r['stock'] <= r['minimum_stock'] %}
                <span class="pill" style="background:#ffe8e2;color:#b6472e;">부족</span>
              {% else %}
                <span class="pill" style="background:#e6f7ed;color:#237447;">정상</span>
              {% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="7" class="muted">등록된 재고가 없습니다.</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <div class="pager">
        {% if page > 1 %}
          <a href="{{ url_for('inventory_status', page=page-1) }}">이전</a>
        {% endif %}
        <span>{{ page }} / {{ pages }}</span>
        {% if page < pages %}
          <a href="{{ url_for('inventory_status', page=page+1) }}">다음</a>
        {% endif %}
      </div>
    </section>
    """

    return render_page(
        title="재고 현황",
        page_title="재고 현황",
        active="inventory",
        content=content,
        rows=sliced,
        page=pg["page"],
        pages=pg["pages"],
    )


@app.route("/history")
def product_history() -> str:
    db = get_db()
    q = request.args.get("q", "").strip()
    product_id = int(request.args.get("product_id", "0") or "0")
    page = int(request.args.get("page", "1"))

    where_sql = ""
    args: tuple[Any, ...] = ()
    if q:
        where_sql = "WHERE p.name LIKE ?"
        args = (f"%{q}%",)

    count = db.execute(f"SELECT COUNT(*) FROM products p {where_sql}", args).fetchone()[0]
    pg = paginate(count, page)
    products = db.execute(
        f"""
        SELECT p.id, p.registered_date, p.name, p.minimum_stock, c.name AS category_name
        FROM products p
        JOIN categories c ON c.id = p.category_id
        {where_sql}
        ORDER BY p.id ASC
        LIMIT ? OFFSET ?
        """,
        args + (pg["per_page"], pg["offset"]),
    ).fetchall()
    stocks = get_stock_map()
    selected = find_product(product_id) if product_id else None

    history_rows: list[sqlite3.Row] = []
    if selected:
        history_rows = db.execute(
            """
            SELECT m.id, m.movement_date, c.name AS category_name, p.name AS product_name, m.movement_type, m.quantity
            FROM movements m
            JOIN products p ON p.id = m.product_id
            JOIN categories c ON c.id = p.category_id
            WHERE p.id = ?
            ORDER BY m.id ASC
            """,
            (selected["id"],),
        ).fetchall()

    running = 0
    history_with_stock: list[dict[str, Any]] = []
    for row in history_rows:
        if row["movement_type"] == "입고":
            running += row["quantity"]
        else:
            running -= row["quantity"]
        history_with_stock.append({**dict(row), "stock_after": running})

    content = """
    <section class="card bg-blue">
      <h3>품목 검색</h3>
      <form class="search-box" method="get">
        <input type="text" name="q" value="{{ q }}" placeholder="제품명을 검색하세요" />
        <button type="submit">검색</button>
      </form>
      <table>
        <thead><tr><th>순번</th><th>등록일자</th><th>분류</th><th>제품명</th><th>현재고</th><th>선택</th></tr></thead>
        <tbody>
        {% for p in products %}
          <tr>
            <td>{{ p['id'] }}</td>
            <td>{{ p['registered_date'] }}</td>
            <td>{{ p['category_name'] }}</td>
            <td>{{ p['name'] }}</td>
            <td>{{ stocks.get(p['id'], 0) }}</td>
            <td><a href="{{ url_for('product_history', q=q, page=page, product_id=p['id']) }}">보기</a></td>
          </tr>
        {% else %}
          <tr><td colspan="6" class="muted">조회 결과가 없습니다.</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <div class="pager">
        {% if page > 1 %}
          <a href="{{ url_for('product_history', q=q, page=page-1, product_id=selected_id) }}">이전</a>
        {% endif %}
        <span>{{ page }} / {{ pages }}</span>
        {% if page < pages %}
          <a href="{{ url_for('product_history', q=q, page=page+1, product_id=selected_id) }}">다음</a>
        {% endif %}
      </div>
    </section>

    <section class="card bg-orange" style="margin-top:14px;">
      <h3>품목별 입출고 내역</h3>
      {% if selected %}
        <p class="muted"><strong>{{ selected['name'] }}</strong> / 분류 {{ selected['category_name'] }}</p>
        <table>
          <thead><tr><th>순번</th><th>등록일자</th><th>분류</th><th>제품명</th><th>입고/출고</th><th>수량</th><th>재고량</th></tr></thead>
          <tbody>
          {% for row in history_rows %}
            <tr>
              <td>{{ row['id'] }}</td>
              <td>{{ row['movement_date'] }}</td>
              <td>{{ row['category_name'] }}</td>
              <td>{{ row['product_name'] }}</td>
              <td>{{ row['movement_type'] }}</td>
              <td>{{ row['quantity'] }}</td>
              <td>{{ row['stock_after'] }}</td>
            </tr>
          {% else %}
            <tr><td colspan="7" class="muted">입출고 내역이 없습니다.</td></tr>
          {% endfor %}
          </tbody>
        </table>
      {% else %}
        <p class="muted">상단에서 제품을 선택해 주세요.</p>
      {% endif %}
    </section>
    """

    return render_page(
        title="품목별 입출고 현황",
        page_title="품목별 입출고 현황",
        active="history",
        content=content,
        q=q,
        products=products,
        stocks=stocks,
        page=pg["page"],
        pages=pg["pages"],
        selected=selected,
        selected_id=product_id,
        history_rows=history_with_stock,
    )


@app.route("/chat")
def chat() -> str:
    content = """
    <section class="card chat-shell">
      <div class="chat-guide">
        <strong>자연어로 재고를 관리하세요</strong>
        <p>입출고 등록, 재고 조회, 신규 제품 등록과 발주 메일 발송을 요청할 수 있습니다.</p>
        <div class="chat-examples">
          <button type="button" class="chat-example" data-message="최소 재고량 이하 제품 알려줘">재고 부족 조회</button>
          <button type="button" class="chat-example" data-message="재고 10개 이하 제품 목록 보여줘">10개 이하 조회</button>
          <button type="button" class="chat-example" data-message="신규 제품 등록">신규 제품 등록</button>
        </div>
      </div>
      <div id="chat-messages" class="chat-messages" aria-live="polite">
        <div class="chat-message agent">
          <div class="chat-bubble">
            <p>안녕하세요. 재고관리 도우미입니다.</p>
            <p>예: <code>USB-C 케이블 10개 입고</code></p>
          </div>
        </div>
      </div>
      <form id="chat-form" class="chat-input">
        <input id="chat-message" type="text" maxlength="500" autocomplete="off"
               placeholder="재고관리 요청을 입력하세요" aria-label="재고관리 요청" required />
        <button id="chat-submit" type="submit">전송</button>
      </form>
    </section>
    <script>
      (() => {
        const form = document.getElementById("chat-form");
        const input = document.getElementById("chat-message");
        const submit = document.getElementById("chat-submit");
        const messages = document.getElementById("chat-messages");

        function appendMessage(role, value, isHtml = false) {
          const row = document.createElement("div");
          row.className = `chat-message ${role}`;
          const bubble = document.createElement("div");
          bubble.className = "chat-bubble";
          if (isHtml) bubble.innerHTML = value;
          else bubble.textContent = value;
          row.appendChild(bubble);
          messages.appendChild(row);
          messages.scrollTop = messages.scrollHeight;
        }

        async function sendMessage(message) {
          const clean = message.trim();
          if (!clean || submit.disabled) return;
          appendMessage("user", clean);
          input.value = "";
          submit.disabled = true;
          submit.textContent = "처리 중";

          try {
            const response = await fetch("{{ url_for('chat_api') }}", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ message: clean })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "요청 처리에 실패했습니다.");
            appendMessage("agent", data.answer_html, true);
          } catch (error) {
            appendMessage("agent", error.message || "서버 연결에 실패했습니다.");
          } finally {
            submit.disabled = false;
            submit.textContent = "전송";
            input.focus();
          }
        }

        form.addEventListener("submit", (event) => {
          event.preventDefault();
          sendMessage(input.value);
        });
        document.querySelectorAll(".chat-example").forEach((button) => {
          button.addEventListener("click", () => sendMessage(button.dataset.message));
        });
      })();
    </script>
    """
    return render_page(
        title="대화창",
        page_title="대화창",
        active="chat",
        content=content,
    )


@app.post("/api/chat")
def chat_api() -> Any:
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify({"error": "메시지를 입력해 주세요."}), 400
    if len(message) > 500:
        return jsonify({"error": "메시지는 500자 이하로 입력해 주세요."}), 400

    try:
        effective_message = message
        if session.get("chat_pending") == "register_product" and (
            "분류" in message or "제품명" in message or "최소재고" in message.replace(" ", "")
        ):
            effective_message = f"신규 제품 등록, {message}"

        result = inventory_agent.invoke(effective_message)
        action = result.pop("action")
        if (
            action["intent"] == "register_product"
            and not result["success"]
            and (
                not action.get("category")
                or not action.get("product_name")
                or action.get("minimum_stock") is None
            )
        ):
            session["chat_pending"] = "register_product"
        else:
            session.pop("chat_pending", None)
        return jsonify(result)
    except Exception:
        app.logger.exception("재고 에이전트 요청 처리 실패")
        return jsonify({"error": "에이전트 요청 처리 중 오류가 발생했습니다."}), 500


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login() -> str:
    message = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_manage"))
        message = "비밀번호가 올바르지 않습니다."

    content = """
    <section class="card bg-blue">
      <h3>관리자 로그인</h3>
      <form method="post">
        <div class="field" style="max-width:340px;">
          <label>비밀번호</label>
          <input type="password" name="password" required />
        </div>
        <div class="line"><button type="submit">로그인</button></div>
      </form>
      <p class="muted">기본 비밀번호: admin1004</p>
    </section>
    """
    return render_page(
        title="관리자 로그인",
        page_title="관리자 로그인",
        active="admin",
        content=content,
        message=message,
    )


@app.route("/admin/logout")
def admin_logout() -> Any:
    session.pop("is_admin", None)
    return redirect(url_for("dashboard"))


@app.route("/admin/manage", methods=["GET", "POST"])
def admin_manage() -> str:
    require_admin()
    db = get_db()
    message = ""

    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "add_category":
            category_name = request.form.get("category_name", "").strip()
            if category_name:
                try:
                    db.execute("INSERT INTO categories(name) VALUES (?)", (category_name,))
                    db.commit()
                    message = "분류가 추가되었습니다."
                except sqlite3.IntegrityError:
                    message = "이미 존재하는 분류입니다."
        elif action == "edit_product":
            product_id = int(request.form.get("product_id", "0"))
            new_name = request.form.get("new_name", "").strip()
            minimum_stock = int(request.form.get("minimum_stock", "0"))
            if product_id and new_name and minimum_stock >= 0:
                try:
                    db.execute(
                        "UPDATE products SET name = ?, minimum_stock = ? WHERE id = ?",
                        (new_name, minimum_stock, product_id),
                    )
                    db.commit()
                    message = "제품 정보가 수정되었습니다."
                except sqlite3.IntegrityError:
                    message = "이미 존재하는 제품명입니다."

    products = db.execute(
        """
        SELECT p.id, p.name, p.minimum_stock, c.name AS category_name
        FROM products p
        JOIN categories c ON c.id = p.category_id
        ORDER BY p.id ASC
        """
    ).fetchall()

    content = """
    <section class="card bg-green">
      <h3>분류 추가</h3>
      <form method="post">
        <input type="hidden" name="action" value="add_category" />
        <div class="line">
          <input type="text" name="category_name" placeholder="새 분류명" required />
          <button type="submit">추가</button>
        </div>
      </form>
    </section>

    <section class="card bg-orange" style="margin-top:14px;">
      <h3>제품명 / 최소재고 수정</h3>
      <table>
        <thead><tr><th>순번</th><th>분류</th><th>제품명</th><th>최소재고</th><th>수정</th></tr></thead>
        <tbody>
        {% for p in products %}
          <tr>
            <td>{{ p['id'] }}</td>
            <td>{{ p['category_name'] }}</td>
            <td>{{ p['name'] }}</td>
            <td>{{ p['minimum_stock'] }}</td>
            <td>
              <form method="post" style="display:flex;gap:6px;">
                <input type="hidden" name="action" value="edit_product" />
                <input type="hidden" name="product_id" value="{{ p['id'] }}" />
                <input type="text" name="new_name" value="{{ p['name'] }}" required />
                <input type="number" min="0" name="minimum_stock" value="{{ p['minimum_stock'] }}" required style="width:90px;" />
                <button type="submit">저장</button>
              </form>
            </td>
          </tr>
        {% else %}
          <tr><td colspan="5" class="muted">등록된 제품이 없습니다.</td></tr>
        {% endfor %}
        </tbody>
      </table>
    </section>
    """
    return render_page(
        title="관리자 설정",
        page_title="관리자 설정",
        active="admin",
        content=content,
        products=products,
        message=message,
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
