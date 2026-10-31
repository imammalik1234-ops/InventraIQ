from flask import Flask, g, request, render_template, redirect, url_for, session
from intelligence import inventory_intelligence
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from dotenv import load_dotenv
from functools import wraps
from datetime import date, datetime, timedelta
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import numpy as np
import pandas as pd
import os
import io
import csv
from flask import Response, render_template, request, redirect, url_for
from datetime import date

from database import db
from models import Alert, Inventory, Product, Sale, User
import inventory_transactions as stock_tx
from security import init_security, transaction, MutationError
from configuration import application_config, ConfigurationError
from readiness import register_readiness
from authz import init_authorization, scoped_query, legacy_predicate, legacy_resource, resolve_scope
from operations import init_operational_errors, log_event, safe_failure
from authentication import (init_authentication, load_authenticated_user, consume_login_attempt,
                            LoginThrottled, AuthenticationUnavailable, validated_profile)


load_dotenv(override=False)


app = Flask(__name__)
app.config.update(application_config())
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

try:
    db.init_app(app)
except (SQLAlchemyError, ValueError, TypeError, ImportError, OSError):
    raise ConfigurationError('Database driver/settings could not be initialized. Check DATABASE_URL and driver options.') from None
register_readiness(app)
init_operational_errors(app)
init_authentication(app)


@app.before_request
def require_configured_https():
    if app.config['HTTPS_REQUIRED'] and not request.is_secure:
        return {'error': 'HTTPS is required for this application.'}, 400

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'role_selection'
init_security(app, login_manager)
init_authorization(app)


@login_manager.user_loader
def load_user(user_id):
    return load_authenticated_user(user_id)


from functools import wraps
from flask_login import current_user
from flask import redirect, url_for

from functools import wraps
from flask_login import current_user
from flask import redirect, url_for

def role_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        def decorated(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("role_selection"))
            # Historical role arguments are layout compatibility, never grants.
            # Recheck here as well for views mounted by another Flask application.
            from authz import READ_ENDPOINTS, WRITE_ENDPOINTS
            safe = request.method in {'GET', 'HEAD', 'OPTIONS'}
            capability = (READ_ENDPOINTS if safe else WRITE_ENDPOINTS).get(fn.__name__)
            if capability:
                g.authz_scope = resolve_scope(capability, write=not safe and capability != 'profile.manage')
            return fn(*args, **kwargs)
        return decorated
    return wrapper


def role_home():
    if not current_user.is_authenticated:
        return redirect(url_for("role_selection"))
    return redirect(url_for("admin_dashboard") if current_user.role == "admin" else url_for("home"))

@app.context_processor
def inject_user_profile():
    if current_user.is_authenticated:
        scope = getattr(g, 'authz_scope', None)
        if scope is None:
            # Assistant review routes authorize inside their service. Resolve the
            # same read scope for shell presentation without granting a write.
            from werkzeug.exceptions import HTTPException
            try:scope=resolve_scope('inventory.read')
            except HTTPException:pass
        name = current_user.name or "Staff Member"
        initials = "".join([part[0].upper() for part in name.split() if part])[:2] or "SM"
        return {
            "profile_name": name,
            "profile_email": current_user.email,
            "profile_initials": initials,
            "authz_role": scope.role if scope else None,
            "can_manage_catalog": bool(scope and scope.role == 'hq_admin'),
            "can_adjust_stock": bool(scope and scope.role in {'hq_admin', 'manager'}),
            "can_propose_actions": bool(scope and not scope.consolidated and scope.role in {'hq_admin','manager'}),
            "branch_scope_label": ('HQ consolidated' if scope.consolidated else f'Branch {scope.branches[0]}') if scope else 'Branch scope',
        }
    return {}


def legacy_catalog_choices():
    """Database-backed legacy selector; never creates new SKU identities."""
    from models import LegacyProductMapping, SKU
    result={}
    query=(scoped_query(Product).join(LegacyProductMapping,LegacyProductMapping.legacy_product_id==Product.product_id)
           .join(SKU,SKU.sku_id==LegacyProductMapping.sku_id).add_entity(SKU))
    for product,sku in query:
        family=(sku.variant_attributes or {}).get('family') or product.name
        item=result.setdefault(product.category or '',{}).setdefault(family,dict(variants=[],suppliers=[]))
        if product.name not in item['variants']:item['variants'].append(product.name)
        if product.supplier and product.supplier not in item['suppliers']:item['suppliers'].append(product.supplier)
    return result


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None

def check_alerts():
    resolve_scope("alerts.evaluate", write=True)
    today = date.today()

    for product in scoped_query(Product).filter_by(archived=False).all():
        inventory = scoped_query(Inventory).filter_by(product_id=product.product_id).first()
        quantity = inventory.quantity if inventory else 0

        # LOW STOCK ALERT
        if quantity <= product.reorder_level:
            existing_low_stock = scoped_query(Alert).filter_by(
                product_id=product.product_id,
                alert_type="LOW_STOCK",
                status="active"
            ).first()

            if existing_low_stock is None:
                low_stock_alert = Alert(
                    product_id=product.product_id,
                    alert_type="LOW_STOCK",
                    message=f"{product.name} is low in stock. Current quantity: {quantity}",
                    status="active",
                    created_at=datetime.now()
                )
                db.session.add(low_stock_alert)

        # EXPIRY ALERT
        if product.expiry_date:
            days_left = (product.expiry_date - today).days

            if 0 <= days_left <= 7:
                existing_expiry = scoped_query(Alert).filter_by(
                    product_id=product.product_id,
                    alert_type="EXPIRY",
                    status="active"
                ).first()

                if existing_expiry is None:
                    expiry_alert = Alert(
                        product_id=product.product_id,
                        alert_type="EXPIRY",
                        message=f"{product.name} is expiring soon. Days left: {days_left}",
                        status="active",
                        created_at=datetime.now()
                    )
                    db.session.add(expiry_alert)

            elif days_left < 0:
                existing_expired = scoped_query(Alert).filter_by(
                    product_id=product.product_id,
                    alert_type="EXPIRED",
                    status="active"
                ).first()

                if existing_expired is None:
                    expired_alert = Alert(
                        product_id=product.product_id,
                        alert_type="EXPIRED",
                        message=f"{product.name} has expired.",
                        status="active",
                        created_at=datetime.now()
                    )
                    db.session.add(expired_alert)

def inventory_for_product(product_id):
    legacy_resource(product_id)
    return scoped_query(Inventory).filter_by(product_id=product_id).first()


def form_int(field_name, default=0):
    try:
        return int(request.form.get(field_name, default))
    except (TypeError, ValueError):
        return default


def product_form_defaults(product=None, inventory=None):
    product_name = product.name if product else ""
    category = product.category if product else ""
    product_value = next((name for name, details in legacy_catalog_choices().get(category, {}).items()
                          if product_name == name or product_name in details['variants']), product_name)

    return {
        "category": category,
        "product": product_value,
        "variant": product_name,
        "supplier": product.supplier if product else "",
        "quantity": str(inventory.quantity if inventory else ""),
        "expiry_date": product.expiry_date.isoformat() if product and product.expiry_date else "",
        "unit_price": str(product.unit_price if product and product.unit_price is not None else ""),
        "reorder_level": str(product.reorder_level if product else 10),
    }


def form_data_from_request():
    fields = ["category", "product", "variant", "supplier", "quantity", "expiry_date", "unit_price", "reorder_level"]
    return {field: request.form.get(field, "").strip() for field in fields}


def exact_product_batch_query(cleaned, existing_product_id=None):
    query = scoped_query(Product).filter_by(
        name=cleaned["name"],
        category=cleaned["category"],
        supplier=cleaned["supplier"],
        expiry_date=cleaned["expiry_date"]
    )

    if existing_product_id is not None:
        query = query.filter(Product.product_id != existing_product_id)

    return query


def add_quantity_to_existing_product(product, quantity):
    product, inventory = stock_tx.receive_stock(product.product_id, quantity)
    create_low_stock_alert(product, inventory)
    create_expiry_alert(product)


def validate_product_form(form_data, require_catalog=True, existing_product_id=None, require_future_expiry=False, check_duplicates=True):
    errors = []
    category = form_data.get("category", "")
    product = form_data.get("product", "")
    variant = form_data.get("variant", "")
    supplier = form_data.get("supplier", "")
    if require_catalog:
        catalog_items = legacy_catalog_choices().get(category, {})
        catalog_item = catalog_items.get(product) or next((item for item in catalog_items.values() if variant in item['variants']), None)
        if catalog_item is None:
            errors.append("Please select a valid category and product.")
        else:
            if variant not in catalog_item["variants"]:
                errors.append("Please select a valid product variant.")
            if supplier not in catalog_item["suppliers"]:
                errors.append("Please select a valid brand.")
    try:
        cleaned = stock_tx.product_values({
            "name": variant if require_catalog else product,
            "category": category, "supplier": supplier,
            "quantity": form_data.get("quantity"), "unit_price": form_data.get("unit_price"),
            "reorder_level": form_data.get("reorder_level"), "expiry_date": form_data.get("expiry_date"),
        })
    except MutationError as exc:
        return errors + [str(exc)], {}
    if require_future_expiry and cleaned["expiry_date"] <= date.today():
        errors.append("Expiry date must be a future date.")
    if check_duplicates and exact_product_batch_query(cleaned, existing_product_id).first():
        errors.append("A product with this category, variant, brand, and expiry date already exists.")
    return errors, cleaned


def render_product_form(template_name, **context):
    context.setdefault("catalog", legacy_catalog_choices())
    context.setdefault("tomorrow", (date.today() + timedelta(days=1)).isoformat())
    context.setdefault("today", date.today().isoformat())
    return render_template(template_name, **context)

def get_product_movement_clusters():
    """
    Analyzes live sales distribution records across all active rows
    to tag items as 'Fast-Moving', 'Stable', or 'Slow-Moving' via K-Means.
    """
    resolve_scope('reports.read')
    try:
        products = scoped_query(Product).filter_by(archived=False).all()
        if len(products) < 3:
            return {p.product_id: "Stable" for p in products}

        data_points = []
        product_ids = []

        sales_totals = dict(db.session.query(Sale.product_id, func.sum(Sale.quantity)).filter(legacy_predicate(Sale, "sales.read")).group_by(Sale.product_id).all())
        for p in products:
            total_sales = sales_totals.get(p.product_id) or 0
            price = float(p.unit_price) if p.unit_price else 0.0

            data_points.append([total_sales, price])
            product_ids.append(p.product_id)

        X = np.array(data_points)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        if len(np.unique(X_scaled, axis=0)) < 3:
            # Stable is a neutral display fallback, not a fitted movement prediction.
            return {product_id: "Stable" for product_id in product_ids}

        kmeans = KMeans(n_clusters=3, init="k-means++", random_state=42, n_init="auto")
        labels = kmeans.fit_predict(X_scaled)

        cluster_averages = []
        for c_id in range(3):
            sales_in_cluster = [X[i][0] for i in range(len(labels)) if labels[i] == c_id]
            avg_sales = np.mean(sales_in_cluster) if sales_in_cluster else 0
            cluster_averages.append((c_id, avg_sales))

        cluster_averages.sort(key=lambda x: x[1])

        rank_mapping = {
            cluster_averages[0][0]: "Slow-Moving",
            cluster_averages[1][0]: "Stable",
            cluster_averages[2][0]: "Fast-Moving"
        }

        return {product_ids[i]: rank_mapping[labels[i]] for i in range(len(product_ids))}
    except Exception as exc:
        log_event('movement_analysis_unavailable', exc)
        return {}

def product_inventory_summary(product, decision, movement_categories):
    quantity = decision["current_stock"]
    reorder_level = product.reorder_level or 0
    days_left = None
    status_labels = []
    low_stock = quantity <= reorder_level
    reorder_needed = (decision["recommended_reorder_qty"] or 0) > 0

    if product.expiry_date:
        days_left = (product.expiry_date - date.today()).days
        if days_left < 0:
            status_labels.append("Expired")
        elif days_left <= 7:
            status_labels.append("Expiring Soon")

    if low_stock:
        status_labels.append("Low Stock")
    if reorder_needed:
        status_labels.append("Reorder Needed")
    if decision["projected_shortfall"]:
        status_labels.append("Projected Shortfall")
    if decision["recommended_reorder_qty"] is None:
        status_labels.append("Review Required")
    if not status_labels:
        status_labels.append("In Stock")

    velocity_tag = movement_categories.get(product.product_id, "Stable")

    return {
        **decision,
        "product_id": product.product_id,
        "version": product.version,
        "product_name": product.name,
        "name": product.name,
        "category": product.category or "",
        "supplier": product.supplier or "",
        "unit_price": float(product.unit_price) if product.unit_price else 0,
        "quantity": quantity,
        "reorder_level": reorder_level,
        "expiry_date": product.expiry_date,
        "expiry_date_display": product.expiry_date.isoformat() if product.expiry_date else "",
        "days_left": days_left,
        "stock_status": status_labels[0],
        "status_labels": status_labels,
        "status_filter": " ".join(status_labels),
        "low_stock": low_stock,
        "reorder_needed": reorder_needed,
        "predicted_demand": decision["forecast"]["predicted_demand"],
        "suggested_reorder": decision["recommended_reorder_qty"],
        "velocity": velocity_tag
    }


def inventory_summaries():
    decisions = inventory_intelligence()
    products = {p.product_id: p for p in scoped_query(Product).all()}
    movement = get_product_movement_clusters()
    return [product_inventory_summary(products[row['product_id']], row, movement) for row in decisions]


def top_selling_items(limit=5):
    sold = func.sum(Sale.quantity).label("sold")
    rows = (db.session.query(Product.name, Product.category, sold)
            .join(Sale, Sale.product_id == Product.product_id)
            .filter(legacy_predicate(Sale, "sales.read"))
            .group_by(Product.product_id, Product.name, Product.category)
            .order_by(sold.desc(), Product.product_id).limit(limit).all())
    return [{"name": row.name, "category": row.category or "", "sold": row.sold} for row in rows]


def admin_dashboard_data():
    summaries = inventory_summaries()
    low_stock = [item for item in summaries if item["low_stock"]]
    expiring = [item for item in summaries if item["days_left"] is not None and 0 <= item["days_left"] <= 7]
    reorder_items = [item for item in summaries if item["reorder_needed"]]

    return {
        "stats": {
            "total_products": len(summaries),
            "low_stock": len(low_stock),
            "expiring": len(expiring),
            "reorder_needed": len(reorder_items),
        },
        "top_selling": top_selling_items(),
        "reorder_items": reorder_items[:5],
        "intelligence_items": summaries,
        "recent_alerts": inventory_alert_items(limit=5, summaries=summaries),
    }


def staff_dashboard_data():
    summaries = inventory_summaries()
    low_stock = [item for item in summaries if item["low_stock"]]
    expiring = [item for item in summaries if item["days_left"] is not None and 0 <= item["days_left"] <= 7]
    reorder_items = [item for item in summaries if item["reorder_needed"]]

    return {
        "stats": {
            "total_products": len(summaries),
            "low_stock": len(low_stock),
            "expiring": len(expiring),
            "reorder_needed": len(reorder_items),
        },
        "low_stock_items": low_stock[:5],
        "expiring_items": expiring[:5],
        "reorder_items": reorder_items[:5],
        "intelligence_items": summaries,
        "top_selling": top_selling_items(),
        "recent_alerts": inventory_alert_items(limit=5, summaries=summaries),
    }


def admin_alert_sections():
    summaries = inventory_summaries()

    low_stock = [item for item in summaries if item["low_stock"]]

    expiring_soon = [
        item for item in summaries
        if item["days_left"] is not None and 0 <= item["days_left"] <= 7
    ]

    reorder_needed = [
        item for item in summaries
        if item["reorder_needed"]
    ]

    return {
        "low_stock": low_stock,
        "expiring_soon": expiring_soon,
        "reorder_needed": reorder_needed,
    }


def recent_admin_alerts(limit=5):
    return inventory_alert_items(limit=limit)


def inventory_alert_items(limit=None, summaries=None):
    alerts = []

    for item in (inventory_summaries() if summaries is None else summaries):
        if item["days_left"] is not None:
            if item["days_left"] < 0:
                alerts.append({
                    "product_name": item["name"],
                    "alert_type": "Expired",
                    "days_remaining": item["days_left"],
                    "detail": "Expired",
                    "priority": "danger",
                    "sort_value": -1000 + item["days_left"],
                })
            elif item["days_left"] <= 7:
                alerts.append({
                    "product_name": item["name"],
                    "alert_type": "Expiring Soon",
                    "days_remaining": item["days_left"],
                    "detail": f"{item['days_left']} day{'s' if item['days_left'] != 1 else ''} remaining",
                    "priority": "danger" if item["days_left"] <= 2 else "warning",
                    "sort_value": item["days_left"],
                })

        if item["reorder_needed"]:
            alerts.append({
                "product_name": item["name"],
                "alert_type": "Reorder Needed",
                "days_remaining": None,
                "detail": f"{item['quantity']} in stock",
                "priority": "warning",
                "sort_value": 20,
            })

    alerts = sorted(alerts, key=lambda alert: (alert["sort_value"], alert["product_name"]))
    return alerts[:limit] if limit else alerts
def create_expiry_alert(product):
    legacy_resource(product.product_id, 'sales.create', write=True)
    if product.expiry_date:
        days_left = (product.expiry_date - date.today()).days

        if days_left <= 7:
            existing_alert = scoped_query(Alert).filter_by(
                product_id=product.product_id,
                alert_type="EXPIRY",
                status="active"
            ).first()

            if existing_alert is None:
                alert = Alert(
                    product_id=product.product_id,
                    alert_type="EXPIRY",
                    message=f"{product.name} is expiring soon. Days left: {days_left}.",
                    status="active",
                    created_at=datetime.now()
                )

                db.session.add(alert)

@app.route("/")
def index():
    return role_home()


@app.route("/dashboard")
@login_required
@role_required("staff")
def home():
    dashboard_data = staff_dashboard_data()

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        stats=dashboard_data["stats"],
        low_stock_items=dashboard_data["low_stock_items"],
        expiring_items=dashboard_data["expiring_items"],
        reorder_items=dashboard_data["reorder_items"],
        top_selling_items=dashboard_data["top_selling"],
        recent_alerts=dashboard_data["recent_alerts"],
        intelligence_items=dashboard_data["intelligence_items"]
    )

@app.route('/staff')
@login_required
@role_required("staff")
def staff():
    return render_template('staff.html')

@app.route('/notify-manager', methods=['POST'])
@login_required
@role_required("staff")
def notify_manager():
    return {"error": "Manager notifications are unavailable. No notification was sent."}, 501


@app.route('/reassign-tasks', methods=['POST'])
@login_required
@role_required("staff")
def reassign_tasks():
    return {"error": "Task reassignment is unavailable. No tasks were changed."}, 501


@app.route('/request-replacement', methods=['POST'])
@login_required
@role_required("staff")
def request_replacement():
    return {"error": "Replacement requests are unavailable. No request was sent."}, 501

@app.route("/roles")
def role_selection():

    if current_user.is_authenticated:
        return role_home()
    return render_template("role_selection.html")

@app.route("/login/admin", methods=["GET", "POST"])
def admin_login():
    return login_response('admin')


@app.route("/login/staff", methods=["GET", "POST"])
def staff_login():
    return login_response('staff')


def login_response(role):
    if current_user.is_authenticated:
        return role_home()
    error = None
    status, retry = 200, None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            consume_login_attempt(email, request.remote_addr or 'unknown')
        except LoginThrottled as exc:
            log_event('login_throttled', level='warning')
            error, status, retry = 'Too many sign-in attempts. Try again later.', 429, exc.retry_after
        except AuthenticationUnavailable as exc:
            return safe_failure('throttle_unavailable', exc)
        if error is None:
            user = User.query.filter_by(email=email).first() if len(email) <= 100 else None
            from werkzeug.security import check_password_hash
            try:
                password_ok = check_password_hash(user.password_hash if user else DUMMY_PASSWORD_HASH, password)
            except (ValueError, TypeError) as exc:
                check_password_hash(DUMMY_PASSWORD_HASH, password)
                log_event('credential_record_invalid', exc)
                password_ok = False
            if user and password_ok and user.enabled and user.role == role:
                session.clear()
                login_user(user, remember=False)
                log_event('login_succeeded', level='info')
                return redirect(url_for('admin_dashboard' if role == 'admin' else 'home'))
            log_event('login_rejected', level='info')
            error = 'Invalid admin credentials.' if role == 'admin' else 'Invalid staff credentials.'
    response = app.make_response((render_template('admin_login.html' if role == 'admin' else 'staff_login.html', error=error), status))
    if retry is not None:
        response.headers['Retry-After'] = str(retry)
    return response


from werkzeug.security import generate_password_hash
DUMMY_PASSWORD_HASH = generate_password_hash(os.urandom(32).hex())

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    try:
        current_user.revoke_sessions()
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise
    finally:
        logout_user()
        session.clear()
    return redirect(url_for("role_selection"))


@app.route("/sales")
@login_required
@role_required("admin")
def sales():
    total_sales = db.session.query(func.sum(Sale.quantity)).filter(legacy_predicate(Sale, "sales.read")).scalar() or 0
    rows = (db.session.query(Sale, Product.name, Product.archived).outerjoin(Product, Sale.product_id == Product.product_id)
            .filter(legacy_predicate(Sale, "sales.read"))
            .order_by(Sale.sale_date.desc(), Sale.sale_id.desc()).limit(100).all())
    sales_records = [{"sale_date": sale.sale_date, "product_name": name or "Unknown product", "quantity": sale.quantity,
                      "archived": bool(archived)} for sale, name, archived in rows]
    return render_template("sales.html", total_sales=total_sales, sales_records=sales_records)


@app.route("/admin-dashboard")
@login_required
@role_required("admin")
def admin_dashboard():
    dashboard_data = admin_dashboard_data()
    summarized_products = dashboard_data["intelligence_items"]
    velocity_counts = {label: sum(p['velocity'] == label for p in summarized_products)
                       for label in ("Fast-Moving", "Stable", "Slow-Moving")}

    return render_template(
        "admin-dashboard.html",
        active_page="dashboard",
        stats=dashboard_data["stats"],
        top_selling=dashboard_data["top_selling"],
        reorder_items=dashboard_data["reorder_items"],
        recent_alerts=dashboard_data["recent_alerts"],
        intelligence_items=dashboard_data["intelligence_items"],
        products=summarized_products,
        counts=velocity_counts
    )


@app.route("/products", methods=["GET"])
@login_required
def get_products():
    products = scoped_query(Product).filter_by(archived=False).all()
    result = []

    for product in products:
        result.append({
            "product_id": product.product_id,
            "name": product.name,
            "category": product.category,
            "supplier": product.supplier,
            "unit_price": float(product.unit_price) if product.unit_price else 0,
            "expiry_date": str(product.expiry_date),
            "reorder_level": product.reorder_level,
            "version": product.version
        })

    return result


@app.route('/reports')
@login_required
@role_required("admin")
def reports():
    return render_template("reports.html", intelligence_items=inventory_intelligence())


def validated_product_payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise MutationError("A JSON object is required")
    for field in ("quantity", "reorder_level"):
        if type(data.get(field)) is not int:
            raise MutationError(field + " must be a whole JSON number")
    return stock_tx.product_values(data)


@app.route('/products', methods=['POST'])
@login_required
@role_required('admin', 'staff')
@transaction
def add_product_api():
    product, inventory = stock_tx.create_product(validated_product_payload())
    create_low_stock_alert(product, inventory)
    create_expiry_alert(product)
    return {"message": "Product added successfully", "product_id": product.product_id, "version": product.version}


@app.route("/products/<int:product_id>", methods=["PUT"])
@login_required
@role_required('admin', 'staff')
@transaction
def update_product_api(product_id):
    values = validated_product_payload()
    product, inventory = stock_tx.edit_product(product_id, values, request.get_json().get('expected_version'))
    create_low_stock_alert(product, inventory)
    create_expiry_alert(product)
    return {"message": "Product updated successfully", "version": product.version}


@app.route("/products/<int:product_id>", methods=["DELETE"])
@login_required
@role_required('admin', 'staff')
@transaction
def delete_product_api(product_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise MutationError("A JSON object with expected_version is required")
    action = stock_tx.delete_product(product_id, data.get('expected_version'), data.get('mode', 'archive'))
    return {"message": "Product " + action + ". Historical records are retained.", "action": action}


@app.route("/inventory", methods=["GET"])
@login_required
def get_inventory():
    inventory_items = scoped_query(Inventory).join(Product, Inventory.product_id == Product.product_id).filter(Product.archived.is_(False)).all()
    products = {product.product_id: product for product in scoped_query(Product).all()}
    result = []

    for item in inventory_items:
        product = products.get(item.product_id)

        result.append({
            "inventory_id": item.inventory_id,
            "product_id": item.product_id,
            "product_name": product.name if product else None,
            "category": product.category if product else None,
            "quantity": item.quantity,
            "last_updated": str(item.last_updated)
        })

    return result

@app.route('/inventory-page')
@login_required
@role_required("staff")
def inventory_page():
    success_message = None
    if request.args.get("updated"):
        success_message = "Existing inventory updated successfully. Stock quantity has been increased."

    return render_template(
        "inventory.html",
        active_page="inventory",
        products=inventory_summaries(),
        success_message=success_message
    )
@app.route("/inventory/all-products")
@login_required
@role_required("staff")
def all_products_page():
    return render_template(
        "inventory_filtered.html",
        page_title="All Products",
        products=inventory_summaries(),
        active_page="inventory"
    )


@app.route("/inventory/low-stock")
@login_required
@role_required("staff")
def low_stock_page():
    products = [item for item in inventory_summaries() if item["low_stock"]]

    return render_template(
        "inventory_filtered.html",
        page_title="Low Stock Items",
        products=products,
        active_page="inventory"
    )


@app.route("/inventory/reorder-needed")
@login_required
@role_required("staff")
def reorder_needed_page():
    products = [item for item in inventory_summaries() if item["reorder_needed"]]

    return render_template(
        "inventory_filtered.html",
        page_title="Reorder Needed",
        products=products,
        active_page="inventory"
    )


@app.route("/alerts/expiring-soon")
@login_required
@role_required("staff")
def expiring_soon_page():
    products = [
        item for item in inventory_summaries()
        if item["days_left"] is not None and 0 <= item["days_left"] <= 7
    ]

    return render_template(
        "expiring_filtered.html",
        page_title="Expiring Soon",
        products=products,
        active_page="alerts"
    )
@app.route("/add-product", methods=["GET", "POST"])
@login_required
@role_required("staff")
@transaction
def add_product():
    form_defaults = {
        "category": "",
        "product": "",
        "variant": "",
        "supplier": "",
        "quantity": "",
        "expiry_date": "",
        "unit_price": "",
        "reorder_level": "10",
    }

    if request.method == "POST":
        form_data = {key: request.form.get(key, form_defaults[key]).strip() for key in form_defaults}
        errors, cleaned = validate_product_form(form_data, check_duplicates=False)

        if errors:
            return render_template(
                "add-product.html",
                active_page="add_product",
                catalog=legacy_catalog_choices(),
                form_data=form_data,
                errors=errors,
                tomorrow=(date.today() + timedelta(days=1)).isoformat(),
                is_admin_page=False
            ), 400

        existing_product = exact_product_batch_query(cleaned).first()

        if existing_product:
            add_quantity_to_existing_product(existing_product, cleaned["quantity"])
            return redirect(url_for("inventory_page", updated=existing_product.product_id))

        new_product, inventory = stock_tx.create_product(cleaned)
        create_low_stock_alert(new_product, inventory)
        create_expiry_alert(new_product)

        return redirect("/inventory-page")

    return render_template(
        "add-product.html",
        active_page="add_product",
        catalog=legacy_catalog_choices(),
        form_data=form_defaults,
        errors=[],
        tomorrow=(date.today() + timedelta(days=1)).isoformat(),
        is_admin_page=False
    )


@app.route("/admin-add-product", methods=["GET", "POST"])
@login_required
@role_required("admin")
@transaction
def admin_add_product():
    form_defaults = product_form_defaults()

    if request.method == "POST":
        form_data = form_data_from_request()
        errors, cleaned = validate_product_form(form_data, require_future_expiry=True, check_duplicates=False)

        if errors:
            return render_product_form(
                "admin-add-product.html",
                active_page="inventory",
                form_data=form_data,
                errors=errors,
                is_admin_page=True
            ), 400

        existing_product = exact_product_batch_query(cleaned).first()

        if existing_product:
            add_quantity_to_existing_product(existing_product, cleaned["quantity"])
            return redirect(url_for("admin_inventory", updated=existing_product.product_id))

        new_product, inventory = stock_tx.create_product(cleaned)
        create_low_stock_alert(new_product, inventory)
        create_expiry_alert(new_product)

        return redirect(url_for("admin_inventory", added=new_product.product_id))

    return render_product_form(
        "admin-add-product.html",
        active_page="inventory",
        form_data=form_defaults,
        errors=[],
        is_admin_page=True
    )

def delete_product_records(product_id):
    return stock_tx.delete_product(product_id, request.form.get('expected_version'), request.form.get('mode', 'archive'))


def create_low_stock_alert(product, inventory):
    legacy_resource(product.product_id, 'sales.create', write=True)
    if inventory.quantity <= product.reorder_level:
        existing_alert = scoped_query(Alert).filter_by(
            product_id=product.product_id,
            alert_type="LOW_STOCK",
            status="active"
        ).first()

        if existing_alert is None:
            alert = Alert(
                product_id=product.product_id,
                alert_type="LOW_STOCK",
                message=f"{product.name} is low in stock. Current quantity: {inventory.quantity}.",
                status="active",
                created_at=datetime.now()
            )

            db.session.add(alert)
@app.route("/edit-product/<int:product_id>", methods=["GET", "POST"])
@login_required
@role_required("staff")
@transaction
def edit_product(product_id):
    product = scoped_query(Product).filter_by(product_id=product_id).first()
    if product is None:
        return redirect("/inventory-page")

    if product.archived:
        return {"error": "Archived products cannot be edited."}, 409
    inventory = inventory_for_product(product_id)

    if request.method == "POST":
        form_data = form_data_from_request()
        errors, cleaned = validate_product_form(form_data, existing_product_id=product_id)

        if errors:
            return render_product_form(
                "edit-product.html",
                form_data=form_data,
                errors=errors,
                product=product,
                quantity=inventory.quantity if inventory else 0,
            ), 400

        product, inventory = stock_tx.edit_product(product_id, cleaned, request.form.get('expected_version'))
        create_low_stock_alert(product, inventory)
        create_expiry_alert(product)
        return redirect("/inventory-page")

    return render_template(
        "edit-product.html",
        catalog=legacy_catalog_choices(),
        form_data=product_form_defaults(product, inventory),
        errors=[],
        today=date.today().isoformat(),
        product=product,
        quantity=inventory.quantity if inventory else 0,
    )


@app.route("/delete-product/<int:product_id>", methods=["POST"])
@login_required
@role_required("staff")
@transaction
def delete_product(product_id):
    delete_product_records(product_id)
    return redirect("/inventory-page")


@app.route("/remove-stock/<int:product_id>", methods=["POST"])
@login_required
@role_required("staff")
@transaction
def remove_stock(product_id):
    product, inventory = stock_tx.deduct_stock(product_id, request.form.get('quantity_to_remove'),
                                              expected_version=request.form.get('expected_version'))
    create_low_stock_alert(product, inventory)
    create_expiry_alert(product)
    return redirect("/inventory-page")


@app.route("/sales", methods=["POST"])
@login_required
@role_required('admin', 'staff')
@transaction
def record_sale():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('product_id')) is not int or type(data.get('quantity')) is not int:
        raise MutationError("Product and quantity must be whole JSON numbers")
    product_id = stock_tx.whole_number(data['product_id'], 'Product', minimum=1)
    product, inventory = stock_tx.deduct_stock(product_id, data['quantity'], sale=True)
    create_low_stock_alert(product, inventory)
    create_expiry_alert(product)
    return {"message": "Sale recorded successfully", "product_id": product_id,
            "quantity_sold": data['quantity'], "remaining_stock": inventory.quantity,
            "version": product.version}


@app.route('/admin-record-sale')
@login_required
@role_required("admin")
def admin_record_sale():
    return render_template("admin-record-sale.html")


@app.route("/check-alerts", methods=["POST"])
@login_required
@transaction
@role_required('admin', 'staff')
def run_alert_check():
    check_alerts()
    return {"message": "Alerts checked successfully"}



@app.route("/alerts", methods=["GET"])
@login_required
def get_alerts():
    alerts = scoped_query(Alert).all()
    products = {product.product_id: product for product in scoped_query(Product).all()}
    result = []

    for alert in alerts:
        product = products.get(alert.product_id)

        result.append({
            "alert_id": alert.alert_id,
            "product_name": product.name if product else None,
            "alert_type": alert.alert_type,
            "product_archived": bool(product and product.archived),
            "message": alert.message,
            "status": alert.status,
            "created_at": str(alert.created_at)
        })

    return result

@app.route('/record-sale')
@login_required
@role_required("staff")
def record_sale_page():
    return render_template('record-sale.html')


@app.route("/expiry-alerts")
@login_required
@role_required("staff")
def expiry_alerts_page():
    sections = admin_alert_sections()

    return render_template(
        'alerts.html',
        low_stock_items=sections["low_stock"],
        expiring_items=sections["expiring_soon"],
        reorder_items=sections["reorder_needed"],
        active_page="alerts"
    )


@app.route("/admin-inventory")
@login_required
@role_required("admin")
def admin_inventory():
    success_message = None
    if request.args.get("added"):
        success_message = "Product added successfully."
    elif request.args.get("updated"):
        success_message = "Existing inventory updated successfully. Stock quantity has been increased."

    return render_template(
        "admin-inventory.html",
        active_page="inventory",
        products=inventory_summaries(),
        success_message=success_message
    )

@app.route("/admin-edit-product/<int:product_id>", methods=["GET", "POST"])
@login_required
@role_required("admin")
@transaction
def admin_edit_product(product_id):
    product = scoped_query(Product).filter_by(product_id=product_id).first_or_404(description="Resource not found.")
    if product.archived:
        return {"error": "Archived products cannot be edited."}, 409
    inventory = scoped_query(Inventory).filter_by(product_id=product.product_id).first()

    if request.method == "POST":
        form_data = {
            "product": request.form.get("name", "").strip(),
            "category": request.form.get("category", "").strip(),
            "supplier": request.form.get("supplier", "").strip(),
            "unit_price": request.form.get("unit_price", "").strip(),
            "expiry_date": request.form.get("expiry_date", "").strip(),
            "reorder_level": request.form.get("reorder_level", "").strip(),
            "quantity": request.form.get("quantity", "").strip(),
            "variant": request.form.get("name", "").strip(),
        }
        errors, cleaned = validate_product_form(form_data, require_catalog=False, existing_product_id=product_id)

        if errors:
            return render_template(
                "admin-edit-product.html",
                product=product,
                inventory=inventory,
                active_page="inventory",
                errors=errors
            ), 400

        product, inventory = stock_tx.edit_product(product_id, cleaned, request.form.get('expected_version'))
        create_low_stock_alert(product, inventory)
        create_expiry_alert(product)
        return redirect("/admin-inventory")

    return render_template(
        "admin-edit-product.html",
        product=product,
        inventory=inventory,
        active_page="inventory",
        errors=[]
    )
@app.route("/admin-alerts")
@login_required
@role_required("admin")
def admin_alerts():
    sections = admin_alert_sections()

    return render_template(
        "admin-alerts.html",
        low_stock_items=sections["low_stock"],
        expiring_items=sections["expiring_soon"],
        reorder_items=sections["reorder_needed"],
        active_page="alerts"
    )
@app.route("/admin-delete-product/<int:product_id>", methods=["POST"])
@login_required
@role_required("admin")
@transaction
def admin_delete_product(product_id):
    delete_product_records(product_id)
    return redirect("/admin-inventory")


@app.route("/api/predictions", methods=["GET"])
@login_required
def api_predictions():
    return inventory_intelligence()


@app.route("/ai-predictions")
@login_required
def ai_predictions():
    return render_template("ai_predictions.html", predictions=inventory_intelligence(),
                           active_page="intelligence",
                           base_template="admin-base.html" if current_user.role == "admin" else "base.html")


@app.route("/admin-settings", methods=["GET", "POST"])
@login_required
@role_required("admin")
@transaction
def admin_settings():
    success_message = None
    error_message = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_profile":
            try:
                name, email = validated_profile(request.form.get('name', ''), request.form.get('email', ''))
            except ValueError as exc:
                error_message = str(exc)
            else:
                existing_user = User.query.filter(User.email == email, User.user_id != current_user.user_id).first()
                if existing_user:
                    error_message = "This email address is already registered to another account."
                else:
                    email_changed = current_user.email != email
                    current_user.name = name
                    current_user.email = email
                    if email_changed:
                        # transaction() commits the security revision before this redirect is sent.
                        logout_user()
                        session.clear()
                        return redirect(url_for('admin_login'))
                    success_message = "Profile settings saved and updated successfully!"

    return render_template(
        "admin-settings.html",
        active_page="settings",
        success_message=success_message,
        error_message=error_message,
        profile_name=current_user.name,
        profile_email=current_user.email
    ), (400 if error_message else 200)


@app.route("/admin-settings/export-data")
@login_required
@role_required("admin")
def export_data():
    products = scoped_query(Product).all()
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Product ID", "Product Name", "Category", "Brand", "Unit Price", "Current Stock", "Expiry Date", "Reorder Level", "Status"])

    inventories = {item.product_id: item for item in scoped_query(Inventory).all()}
    def spreadsheet_text(value):
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value

    for product in products:
        inventory = inventories.get(product.product_id)
        current_stock = inventory.quantity if inventory else 0

        writer.writerow([
            product.product_id,
            spreadsheet_text(product.name),
            spreadsheet_text(product.category or "N/A"),
            spreadsheet_text(product.supplier or "N/A"),
            f"{product.unit_price:.2f}" if product.unit_price else "0.00",
            current_stock,
            product.expiry_date if product.expiry_date else "N/A",
            product.reorder_level,
            "Archived" if product.archived else "Active"
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=inventraiq_inventory_{date.today()}.csv"}
    )

@app.route("/analytics")
@login_required
@role_required("admin", "staff")
def analytics():
    # Presentation-only aggregation. Forecast and recommendation logic remains shared.
    today = date.today()
    start = today - timedelta(days=29)
    recorded = dict(db.session.query(Sale.sale_date, func.sum(Sale.quantity))
                    .filter(legacy_predicate(Sale, "sales.read"), Sale.sale_date >= start, Sale.sale_date <= today)
                    .group_by(Sale.sale_date).all())
    trend = [{"date": (start + timedelta(days=i)).isoformat(),
              "units": recorded.get(start + timedelta(days=i), 0)} for i in range(30)]
    return render_template("analytics.html", intelligence_items=inventory_summaries(),
                           trend=trend, total_units=sum(row["units"] for row in trend), active_page="analytics")


@app.errorhandler(404)
@app.errorhandler(403)
def workspace_error(error):
    if request.is_json:
        return {"error": error.description}, error.code
    return render_template("error.html", code=error.code, description=error.description), error.code




@app.route('/archived-products')
@login_required
@role_required('admin', 'staff')
def archived_products():
    return render_template('archived_products.html', products=scoped_query(Product).filter_by(archived=True).order_by(Product.product_id).all(), active_page='inventory')

# T05 compatibility/admin interfaces. All writes retain the shared CSRF boundary.
from flask import jsonify
import branch_services


@app.route('/api/scope', methods=['GET', 'POST'])
@login_required
def scope_selection():
    if request.method == 'GET':
        return branch_services.available_scopes()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) - {'branch_id', 'consolidated'}:
        return {'error': 'Invalid scope selection.'}, 400
    if type(data.get('consolidated', False)) is not bool or ('branch_id' in data and data.get('consolidated')):
        return {'error': 'Invalid scope selection.'}, 400
    return branch_services.select_scope(data.get('branch_id'), data.get('consolidated', False))


@app.route('/api/admin/branches', methods=['GET', 'POST'], defaults={'branch_id': None})
@app.route('/api/admin/branches/<int:branch_id>', methods=['PATCH'])
@login_required
@transaction
def branch_admin(branch_id):
    if request.method == 'GET':
        return branch_services.manage_branch()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return {'error': 'A branch object is required.'}, 400
    return branch_services.manage_branch(branch_id, **data)


@app.route('/api/admin/assignments', methods=['GET', 'PUT'])
@login_required
@transaction
def assignment_admin():
    if request.method == 'GET':
        return branch_services.assignment_directory()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'user_id', 'branch_id', 'branch_role', 'active'}:
        return {'error': 'An explicit assignment object is required.'}, 400
    return branch_services.manage_assignment(**data)


@app.route('/api/admin/hq-scopes', methods=['PUT'])
@login_required
@transaction
def hq_scope_admin():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'user_id', 'hq_role', 'active'}:
        return {'error': 'An explicit HQ assignment object is required.'}, 400
    return branch_services.manage_hq_scope(**data)


@app.route('/api/branch-stock')
@login_required
def branch_stock_api():
    return branch_services.stock_rows()


@app.route('/api/stock-lots/<int:lot_id>')
@login_required
def lot_api(lot_id):
    return branch_services.lot_detail(lot_id)

@app.route('/scope', methods=['GET', 'POST'])
@login_required
def scope_page():
    if request.method == 'POST':
        choice = request.form.get('branch_id')
        branch_services.select_scope(None if choice == 'all' else choice, choice == 'all')
        target=request.form.get('next','')
        from urllib.parse import urlsplit
        try:
            parsed=urlsplit(target)
            safe=(target.startswith('/') and not target.startswith('//') and '\\' not in target
                  and not parsed.scheme and not parsed.netloc and len(target)<=2000
                  and not any(ord(c)<32 for c in target))
        except ValueError:
            safe=False
        if not safe:target=url_for('home')
        return redirect(target)
    return render_template('scope.html', scopes=branch_services.available_scopes(), active_page='scope')


@app.route('/branch-administration', methods=['GET', 'POST'])
@login_required
@transaction
def branch_administration():
    if request.method == 'POST':
        data = request.form
        active = data.get('active') == 'true'
        if data.get('action') == 'branch':
            branch_services.manage_branch(data.get('branch_id') or None, code=data.get('code'),
                display_name=data.get('display_name'), timezone=data.get('timezone'), active=active)
        elif data.get('action') == 'assignment':
            branch_services.manage_assignment(data.get('user_id'), data.get('branch_id'), data.get('branch_role'), active)
        elif data.get('action') == 'hq':
            branch_services.manage_hq_scope(data.get('user_id'), data.get('hq_role'), active)
        else:
            return {'error': 'Invalid administration action.'}, 400
        return redirect(url_for('branch_administration'))
    return render_template('branch_administration.html', branches=branch_services.manage_branch(),
                           directory=branch_services.assignment_directory(), active_page='branches')


from catalog_routes import bp as catalog_blueprint
app.register_blueprint(catalog_blueprint)
from sales_routes import bp as sales_blueprint
app.register_blueprint(sales_blueprint)
from transfer_routes import bp as transfer_blueprint
app.register_blueprint(transfer_blueprint)
from purchasing_routes import bp as purchasing_blueprint
app.register_blueprint(purchasing_blueprint)
from operational_routes import init_operations
init_operations(app)
from assistant.routes import init_app as init_assistant_tools
init_assistant_tools(app)

if __name__ == "__main__":
    app.run(debug=app.config['DEBUG'])
