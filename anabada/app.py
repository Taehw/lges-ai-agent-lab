import os
import secrets
import uuid
from urllib.parse import urlparse

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from models import STATUS_AVAILABLE, STATUS_DONE, Item, User, db

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
ITEMS_PER_PAGE = 12

# ---------------------------------------------------------------------------
# App 설정
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(BASE_DIR, "anabada.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    UPLOAD_FOLDER=os.path.join(BASE_DIR, "static", "uploads"),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # 업로드 최대 5MB
)
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "로그인이 필요한 서비스입니다."
login_manager.login_message_category = "warning"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


# ---------------------------------------------------------------------------
# CSRF 보호 (간단 구현: 세션 토큰 + 모든 POST 요청 검증)
# ---------------------------------------------------------------------------
def get_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(16)
    return session["_csrf_token"]


app.jinja_env.globals["csrf_token"] = get_csrf_token


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("_csrf_token")
        if not token or token != request.form.get("csrf_token"):
            abort(400, description="잘못된 요청입니다. (CSRF 토큰 불일치)")


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_image(file_storage):
    """업로드된 이미지를 저장하고 저장된 파일명을 반환. 파일이 없으면 None."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError("이미지 파일(png, jpg, jpeg, gif, webp)만 업로드할 수 있습니다.")
    # secure_filename은 한글을 제거하므로 확장자만 취하고 uuid로 고유 이름 생성
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[-1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return filename


def delete_image(filename):
    if filename:
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        if os.path.exists(path):
            os.remove(path)


def parse_price(form):
    """폼에서 가격을 읽는다. 무료 나눔 체크 시 0."""
    if form.get("is_free"):
        return 0
    raw = form.get("price", "").replace(",", "").strip()
    if not raw:
        return 0
    if not raw.isdigit():
        raise ValueError("가격은 0 이상의 숫자로 입력해 주세요.")
    return int(raw)


def get_own_item_or_403(item_id):
    item = db.get_or_404(Item, item_id)
    if item.author_id != current_user.id:
        abort(403)
    return item


def is_safe_next_url(target):
    if not target:
        return False
    parsed = urlparse(target)
    return not parsed.netloc and not parsed.scheme and target.startswith("/")


# ---------------------------------------------------------------------------
# 회원 관리
# ---------------------------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")

        error = None
        if not (2 <= len(username) <= 50):
            error = "사용자 이름은 2~50자로 입력해 주세요."
        elif len(password) < 4:
            error = "비밀번호는 4자 이상이어야 합니다."
        elif password != password2:
            error = "비밀번호가 일치하지 않습니다."
        elif User.query.filter_by(username=username).first():
            error = "이미 사용 중인 사용자 이름입니다."

        if error:
            flash(error, "danger")
            return render_template("register.html", username=username)

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("회원가입이 완료되었습니다. 로그인해 주세요.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash("사용자 이름 또는 비밀번호가 올바르지 않습니다.", "danger")
            return render_template("login.html", username=username)

        login_user(user, remember=bool(request.form.get("remember")))
        flash(f"{user.username}님, 환영합니다!", "success")
        next_url = request.args.get("next")
        return redirect(next_url if is_safe_next_url(next_url) else url_for("index"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# 물품 목록 / 검색 / 필터
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    free_only = request.args.get("free") == "1"
    hide_done = request.args.get("hide_done") == "1"
    page = request.args.get("page", 1, type=int)

    query = Item.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Item.title.ilike(like), Item.description.ilike(like)))
    if free_only:
        query = query.filter(Item.price == 0)
    if hide_done:
        query = query.filter(Item.status == STATUS_AVAILABLE)

    pagination = db.paginate(
        query.order_by(Item.created_at.desc()), page=page, per_page=ITEMS_PER_PAGE
    )
    return render_template(
        "index.html",
        items=pagination.items,
        pagination=pagination,
        q=q,
        free_only=free_only,
        hide_done=hide_done,
    )


# ---------------------------------------------------------------------------
# 물품 CRUD
# ---------------------------------------------------------------------------
@app.route("/items/<int:item_id>")
def item_detail(item_id):
    item = db.get_or_404(Item, item_id)
    return render_template("item_detail.html", item=item)


@app.route("/items/new", methods=["GET", "POST"])
@login_required
def item_create():
    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()
        description = form.get("description", "").strip()

        try:
            if not title or not description:
                raise ValueError("제목과 설명을 모두 입력해 주세요.")
            price = parse_price(form)
            image_filename = save_image(request.files.get("image"))
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("item_form.html", item=None, form=form)

        item = Item(
            title=title,
            description=description,
            price=price,
            image_filename=image_filename,
            status=STATUS_AVAILABLE,
            author_id=current_user.id,
        )
        db.session.add(item)
        db.session.commit()
        flash("물품이 등록되었습니다.", "success")
        return redirect(url_for("item_detail", item_id=item.id))

    return render_template("item_form.html", item=None, form={})


@app.route("/items/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def item_edit(item_id):
    item = get_own_item_or_403(item_id)

    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()
        description = form.get("description", "").strip()

        try:
            if not title or not description:
                raise ValueError("제목과 설명을 모두 입력해 주세요.")
            price = parse_price(form)
            new_image = save_image(request.files.get("image"))
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("item_form.html", item=item, form=form)

        if new_image:
            delete_image(item.image_filename)
            item.image_filename = new_image
        elif form.get("remove_image"):
            delete_image(item.image_filename)
            item.image_filename = None

        item.title = title
        item.description = description
        item.price = price
        db.session.commit()
        flash("물품 정보가 수정되었습니다.", "success")
        return redirect(url_for("item_detail", item_id=item.id))

    form = {
        "title": item.title,
        "description": item.description,
        "price": item.price,
        "is_free": item.is_free,
    }
    return render_template("item_form.html", item=item, form=form)


@app.route("/items/<int:item_id>/delete", methods=["POST"])
@login_required
def item_delete(item_id):
    item = get_own_item_or_403(item_id)
    delete_image(item.image_filename)
    db.session.delete(item)
    db.session.commit()
    flash("물품이 삭제되었습니다.", "info")
    return redirect(url_for("index"))


@app.route("/items/<int:item_id>/toggle-status", methods=["POST"])
@login_required
def item_toggle_status(item_id):
    item = get_own_item_or_403(item_id)
    item.status = STATUS_AVAILABLE if item.is_done else STATUS_DONE
    db.session.commit()
    flash(f"상태가 '{item.status}'(으)로 변경되었습니다.", "success")
    return redirect(url_for("item_detail", item_id=item.id))


@app.route("/my-items")
@login_required
def my_items():
    items = current_user.items.order_by(Item.created_at.desc()).all()
    return render_template("my_items.html", items=items)


# ---------------------------------------------------------------------------
# 에러 핸들러
# ---------------------------------------------------------------------------
@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="권한이 없습니다. 작성자만 접근할 수 있습니다."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="요청하신 페이지를 찾을 수 없습니다."), 404


@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400, message=e.description), 400


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, message="파일 용량이 너무 큽니다. (최대 5MB)"), 413


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
