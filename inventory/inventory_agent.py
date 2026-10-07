from __future__ import annotations

import os
import re
import smtplib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Iterator, Literal, TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

Intent = Literal[
    "register_product",
    "stock_in",
    "stock_out",
    "low_stock",
    "history",
    "threshold",
    "send_order",
    "unknown",
]


class InventoryAction(BaseModel):
    intent: Intent = Field(description="사용자의 재고관리 의도")
    product_name: str | None = Field(default=None, description="제품명")
    category: str | None = Field(default=None, description="분류")
    quantity: int | None = Field(default=None, ge=0, description="입출고 수량")
    minimum_stock: int | None = Field(default=None, ge=0, description="최소 재고량")
    threshold: int | None = Field(default=None, ge=0, description="재고 조회 기준")
    email: str | None = Field(default=None, description="발주서를 받을 이메일 주소")


class AgentState(TypedDict, total=False):
    message: str
    action: dict[str, object]
    answer_html: str
    success: bool


class InventoryAgent:
    """LangGraph로 자연어 요청을 해석하고 검증된 재고 작업을 실행한다."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        builder = StateGraph(AgentState)
        builder.add_node("understand", self._understand)
        builder.add_node("execute", self._execute)
        builder.add_edge(START, "understand")
        builder.add_edge("understand", "execute")
        builder.add_edge("execute", END)
        self.graph = builder.compile()

    def invoke(self, message: str) -> dict[str, object]:
        result = self.graph.invoke({"message": message.strip()})
        return {
            "answer_html": result["answer_html"],
            "success": result["success"],
            "action": result["action"],
        }

    def _understand(self, state: AgentState) -> dict[str, dict[str, object]]:
        message = state["message"].strip()
        action = self._rule_based_plan(message)
        if action is None:
            action = self._llm_plan(message)
        return {"action": action.model_dump()}

    def _rule_based_plan(self, message: str) -> InventoryAction | None:
        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", message)
        threshold_match = re.search(r"재고(?:량)?\s*(\d+)\s*개?\s*이하", message)

        if "발주서" in message and ("메일" in message or "이메일" in message):
            return InventoryAction(
                intent="send_order",
                threshold=int(threshold_match.group(1)) if threshold_match else 10,
                email=email_match.group(0) if email_match else None,
            )

        if "최소" in message and "재고" in message and (
            "이하" in message or "부족" in message or "알려" in message
        ):
            return InventoryAction(intent="low_stock")

        history_match = re.search(r"(.+?)\s*(?:의\s*)?입출고\s*내역", message)
        if history_match:
            product_name = self._clean_product_name(history_match.group(1))
            return InventoryAction(intent="history", product_name=product_name or None)

        if threshold_match and ("목록" in message or "제품" in message or "보여" in message):
            return InventoryAction(intent="threshold", threshold=int(threshold_match.group(1)))

        movement_match = re.search(r"(.+?)\s+(\d+)\s*개\s*(입고|출고)", message)
        if movement_match:
            product_name = self._clean_product_name(movement_match.group(1))
            return InventoryAction(
                intent="stock_in" if movement_match.group(3) == "입고" else "stock_out",
                product_name=product_name or None,
                quantity=int(movement_match.group(2)),
            )

        if "신규" in message and "제품" in message and "등록" in message:
            category_match = re.search(r"분류\s*[:：]?\s*([^,\n]+?)(?=\s+제품명|,\s*제품명|$)", message)
            name_match = re.search(
                r"제품명\s*[:：]?\s*(.+?)(?=\s+최소\s*재고|,\s*최소\s*재고|$)",
                message,
            )
            minimum_match = re.search(r"최소\s*재고(?:량)?\s*[:：]?\s*(\d+)", message)
            return InventoryAction(
                intent="register_product",
                category=category_match.group(1).strip() if category_match else None,
                product_name=name_match.group(1).strip() if name_match else None,
                minimum_stock=int(minimum_match.group(1)) if minimum_match else None,
            )

        return None

    @staticmethod
    def _clean_product_name(value: str) -> str:
        value = re.sub(r"^(?:제품\s+)", "", value.strip())
        return value.strip(" ,:：")

    @staticmethod
    def _llm_plan(message: str) -> InventoryAction:
        if not os.getenv("OPENAI_API_KEY"):
            return InventoryAction(intent="unknown")

        model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        planner = model.with_structured_output(InventoryAction)
        return planner.invoke(
            [
                (
                    "system",
                    """
                    너는 소형창고 재고관리 요청 분석기다. 사용자의 한국어 요청을 지정된 스키마로만 분류하라.
                    지원 의도:
                    - register_product: 신규 제품 등록
                    - stock_in / stock_out: 제품 입고 / 출고
                    - low_stock: 현재고가 최소재고량 미만인 제품
                    - history: 특정 제품 입출고 내역
                    - threshold: 특정 수량 이하 제품 목록
                    - send_order: 특정 수량 이하 제품 발주서 이메일 발송
                    - unknown: 그 외 요청
                    없는 값은 추측하지 말고 null로 둔다. SQL은 생성하지 않는다.
                    """,
                ),
                ("human", message),
            ]
        )

    def _execute(self, state: AgentState) -> dict[str, object]:
        action = InventoryAction.model_validate(state["action"])
        handlers = {
            "register_product": self._register_product,
            "stock_in": lambda value: self._record_movement(value, "입고"),
            "stock_out": lambda value: self._record_movement(value, "출고"),
            "low_stock": self._list_low_stock,
            "history": self._product_history,
            "threshold": self._list_by_threshold,
            "send_order": self._send_order_email,
            "unknown": self._unknown,
        }
        try:
            answer_html, success = handlers[action.intent](action)
        except (sqlite3.Error, smtplib.SMTPException, OSError) as exc:
            answer_html = (
                "<p>요청 처리 중 오류가 발생했습니다.</p>"
                f"<p class=\"muted\">{escape(str(exc))}</p>"
            )
            success = False
        return {"answer_html": answer_html, "success": success}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _register_product(self, action: InventoryAction) -> tuple[str, bool]:
        missing = []
        if not action.category:
            missing.append("분류")
        if not action.product_name:
            missing.append("제품명")
        if action.minimum_stock is None:
            missing.append("최소재고량")
        if missing:
            return (
                "<p>신규 제품 등록에 다음 정보가 필요합니다: "
                f"<strong>{escape(', '.join(missing))}</strong></p>"
                "<p>예: <code>신규 제품 등록, 분류 공구 제품명 고무망치 최소재고량 3</code></p>",
                False,
            )

        with self._database() as db:
            category = db.execute(
                "SELECT id FROM categories WHERE name = ? COLLATE NOCASE",
                (action.category,),
            ).fetchone()
            if category is None:
                categories = [
                    row["name"] for row in db.execute("SELECT name FROM categories ORDER BY name")
                ]
                return (
                    f"<p><strong>{escape(action.category)}</strong> 분류가 없습니다.</p>"
                    f"<p>사용 가능한 분류: {escape(', '.join(categories))}</p>",
                    False,
                )
            try:
                db.execute(
                    """
                    INSERT INTO products(registered_date, category_id, name, minimum_stock)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        datetime.now().strftime("%Y-%m-%d"),
                        category["id"],
                        action.product_name,
                        action.minimum_stock,
                    ),
                )
            except sqlite3.IntegrityError:
                return (
                    f"<p><strong>{escape(action.product_name)}</strong> 제품은 이미 등록되어 있습니다.</p>",
                    False,
                )

        return (
            f"<p><strong>{escape(action.product_name)}</strong> 제품을 등록했습니다.</p>"
            f"<p>분류: {escape(action.category)} · 최소재고량: {action.minimum_stock}개 · 현재고: 0개</p>",
            True,
        )

    def _record_movement(
        self, action: InventoryAction, movement_type: Literal["입고", "출고"]
    ) -> tuple[str, bool]:
        if not action.product_name or not action.quantity:
            return (
                f"<p>{movement_type}할 <strong>제품명과 수량</strong>을 입력해 주세요.</p>"
                f"<p>예: <code>USB-C 케이블 10개 {movement_type}</code></p>",
                False,
            )

        with self._database() as db:
            product = db.execute(
                "SELECT id, name FROM products WHERE name = ? COLLATE NOCASE",
                (action.product_name,),
            ).fetchone()
            if product is None:
                return (
                    f"<p><strong>{escape(action.product_name)}</strong> 제품이 DB에 없습니다.</p>"
                    "<p>먼저 <code>신규 제품 등록</code>을 요청해 주세요.</p>",
                    False,
                )

            stock = self._current_stock(db, product["id"])
            if movement_type == "출고" and action.quantity > stock:
                return (
                    f"<p>출고 수량({action.quantity}개)이 현재고({stock}개)보다 많습니다.</p>",
                    False,
                )

            db.execute(
                """
                INSERT INTO movements(movement_date, product_id, movement_type, quantity)
                VALUES (?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime("%Y-%m-%d"),
                    product["id"],
                    movement_type,
                    action.quantity,
                ),
            )
            stock_after = stock + action.quantity if movement_type == "입고" else stock - action.quantity

        return (
            f"<p><strong>{escape(product['name'])}</strong> {action.quantity}개 {movement_type}를 등록했습니다.</p>"
            f"<p>처리 후 재고: <strong>{stock_after}개</strong></p>",
            True,
        )

    @staticmethod
    def _current_stock(db: sqlite3.Connection, product_id: int) -> int:
        row = db.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN movement_type = '입고' THEN quantity ELSE -quantity END
            ), 0) AS stock
            FROM movements
            WHERE product_id = ?
            """,
            (product_id,),
        ).fetchone()
        return int(row["stock"])

    def _stock_rows(self, condition: str, parameter: int | None = None) -> list[sqlite3.Row]:
        params: tuple[int, ...] = () if parameter is None else (parameter,)
        with self._database() as db:
            return db.execute(
                f"""
                SELECT
                    c.name AS category_name,
                    p.name AS product_name,
                    p.minimum_stock,
                    COALESCE(SUM(
                        CASE WHEN m.movement_type = '입고' THEN m.quantity ELSE -m.quantity END
                    ), 0) AS stock
                FROM products p
                JOIN categories c ON c.id = p.category_id
                LEFT JOIN movements m ON m.product_id = p.id
                GROUP BY p.id
                HAVING {condition}
                ORDER BY stock ASC, p.name ASC
                """,
                params,
            ).fetchall()

    def _list_low_stock(self, _action: InventoryAction) -> tuple[str, bool]:
        rows = self._stock_rows("stock < p.minimum_stock")
        return self._render_stock_table(rows, "최소 재고량 미만 제품"), True

    def _list_by_threshold(self, action: InventoryAction) -> tuple[str, bool]:
        threshold = 10 if action.threshold is None else action.threshold
        rows = self._stock_rows("stock <= ?", threshold)
        return self._render_stock_table(rows, f"재고 {threshold}개 이하 제품"), True

    @staticmethod
    def _render_stock_table(rows: list[sqlite3.Row], title: str) -> str:
        if not rows:
            return f"<p>{escape(title)}이 없습니다.</p>"
        body = "".join(
            "<tr>"
            f"<td>{escape(row['category_name'])}</td>"
            f"<td>{escape(row['product_name'])}</td>"
            f"<td>{row['stock']}</td>"
            f"<td>{row['minimum_stock']}</td>"
            "</tr>"
            for row in rows
        )
        return (
            f"<p><strong>{escape(title)}</strong> {len(rows)}건입니다.</p>"
            '<div class="chat-table-wrap"><table><thead><tr>'
            "<th>분류</th><th>제품명</th><th>현재고</th><th>최소재고</th>"
            f"</tr></thead><tbody>{body}</tbody></table></div>"
        )

    def _product_history(self, action: InventoryAction) -> tuple[str, bool]:
        if not action.product_name:
            return (
                "<p>조회할 제품명을 입력해 주세요.</p>"
                "<p>예: <code>USB-C 케이블 입출고 내역 보여줘</code></p>",
                False,
            )

        with self._database() as db:
            product = db.execute(
                "SELECT id, name FROM products WHERE name = ? COLLATE NOCASE",
                (action.product_name,),
            ).fetchone()
            if product is None:
                return (
                    f"<p><strong>{escape(action.product_name)}</strong> 제품이 DB에 없습니다.</p>"
                    "<p>먼저 신규 제품으로 등록해 주세요.</p>",
                    False,
                )
            rows = db.execute(
                """
                SELECT movement_date, movement_type, quantity
                FROM movements
                WHERE product_id = ?
                ORDER BY id ASC
                """,
                (product["id"],),
            ).fetchall()

        if not rows:
            return f"<p><strong>{escape(product['name'])}</strong>의 입출고 내역이 없습니다.</p>", True

        running = 0
        body_parts = []
        for row in rows:
            running += row["quantity"] if row["movement_type"] == "입고" else -row["quantity"]
            body_parts.append(
                "<tr>"
                f"<td>{escape(row['movement_date'])}</td>"
                f"<td>{escape(row['movement_type'])}</td>"
                f"<td>{row['quantity']}</td>"
                f"<td>{running}</td>"
                "</tr>"
            )
        return (
            f"<p><strong>{escape(product['name'])}</strong> 입출고 내역입니다.</p>"
            '<div class="chat-table-wrap"><table><thead><tr>'
            "<th>일자</th><th>입출고</th><th>수량</th><th>처리 후 재고</th>"
            f"</tr></thead><tbody>{''.join(body_parts)}</tbody></table></div>",
            True,
        )

    def _send_order_email(self, action: InventoryAction) -> tuple[str, bool]:
        threshold = 10 if action.threshold is None else action.threshold
        if not action.email or not re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", action.email):
            return (
                "<p>발주서를 받을 올바른 이메일 주소를 입력해 주세요.</p>"
                "<p>예: <code>재고 10개 이하 제품 발주서를 example@naver.com으로 메일 발송해줘</code></p>",
                False,
            )

        rows = self._stock_rows("stock <= ?", threshold)
        if not rows:
            return f"<p>재고 {threshold}개 이하인 제품이 없어 메일을 발송하지 않았습니다.</p>", True

        sender = os.getenv("GMAIL_ID", "iksangyoo@gmail.com")
        password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
        if not password or password == "여기에_구글_앱_비밀번호_입력":
            return (
                "<p>Gmail 앱 비밀번호가 설정되지 않아 메일을 발송할 수 없습니다.</p>"
                "<p><code>.env</code> 파일의 <code>GMAIL_APP_PASSWORD</code>에 "
                "Google 앱 비밀번호를 입력해 주세요.</p>",
                False,
            )

        due_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        item_lines = "\n".join(
            f"- {row['product_name']} ({row['category_name']}): 현재고 {row['stock']}개, "
            f"최소재고 {row['minimum_stock']}개"
            for row in rows
        )
        body = (
            "안녕하세요.\n\n"
            f"재고 {threshold}개 이하 제품의 발주를 정중히 요청드립니다.\n\n"
            f"{item_lines}\n\n"
            f"업무에 참고하시어 {due_date}까지 입고해 주시면 감사하겠습니다.\n"
            "확인 후 회신 부탁드립니다.\n\n감사합니다."
        )
        email = EmailMessage()
        email["Subject"] = f"[재고관리] 재고 {threshold}개 이하 제품 발주 요청"
        email["From"] = sender
        email["To"] = action.email
        email.set_content(body)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.send_message(email)

        return (
            f"<p><strong>{escape(action.email)}</strong>로 발주서를 발송했습니다.</p>"
            f"<p>대상 제품: {len(rows)}건 · 입고 요청일: {due_date}</p>"
            + self._render_stock_table(rows, f"발주 대상(재고 {threshold}개 이하)"),
            True,
        )

    @staticmethod
    def _unknown(_action: InventoryAction) -> tuple[str, bool]:
        return (
            "<p>요청을 이해하지 못했습니다. 아래처럼 입력해 보세요.</p>"
            "<ul>"
            "<li><code>USB-C 케이블 10개 입고</code></li>"
            "<li><code>재고 10개 이하 제품 목록 보여줘</code></li>"
            "<li><code>최소 재고량 이하 제품 알려줘</code></li>"
            "</ul>",
            False,
        )
