from flask import Flask, abort, g, jsonify, render_template, url_for, redirect, request
import os
import stripe
from storedatabase import (
    add_basket,
    claim_order_notification,
    complete_order_notification,
    release_order_notification,
    retrieve,
    retrieveprice,
)
from uuid import UUID, uuid4
from flask_mail import Mail, Message

# Product entries are (display name, display price, URL slug, Stripe Price ID).
app = Flask(__name__, template_folder="templates", static_folder="static")
tshirt = [('Black T-Shirt', '19.99', 'tshirt_black', 'price_1PLOnp06v59uVt9ausadZRkg'), ('White T-Shirt', '19.99', 'tshirt_white', 'price_1PLOnp06v59uVt9ausadZRkg')]
hoodie = [('Charcoal Hoodie', '39.99', 'hoodie_charcoal', 'price_1PU7vd06v59uVt9aLuScITPm'), ('White Hoodie', '39.99', 'hoodie_white', 'price_1PU7vd06v59uVt9aLuScITPm')]

# Mail and domain settings are environment-configurable so deployments do not
# need environment-specific credentials or URLs in the source code.
app.config.update(
    MAIL_SERVER=os.environ.get("MAIL_SERVER", "smtp.mailersend.net"),
    MAIL_PORT=int(os.environ.get("MAIL_PORT", "587")),
    MAIL_USERNAME=os.environ.get("MAIL_USERNAME"),
    MAIL_PASSWORD=os.environ.get("MAIL_PASSWORD"),
    MAIL_USE_TLS=os.environ.get("MAIL_USE_TLS", "true").lower() == "true",
    MAIL_USE_SSL=os.environ.get("MAIL_USE_SSL", "false").lower() == "true",
    MAIL_DEFAULT_SENDER=os.environ.get("MAIL_DEFAULT_SENDER"),
)
mail = Mail(app)
YOUR_DOMAIN = os.environ.get("YOUR_DOMAIN", "http://127.0.0.1:5000")


def required_setting(name):
    # Return the required environment value or stop with a configuration error.
    value = os.environ.get(name)
    if not value:
        app.logger.error("Required environment variable %s is not configured", name)
        abort(503, description=f"Server configuration is missing: {name}")
    return value


@app.before_request
def assign_session_id():
    # Give each browser a stable, versioned ID used to scope its basket.
    cookie_session_id = request.cookies.get("session_id")
    try:
        # Keep cart ownership on the versioned cookie format and normalize UUIDs.
        if not cookie_session_id or not cookie_session_id.startswith("v2-"):
            raise ValueError("Missing or outdated session cookie")
        g.session_id = f"v2-{UUID(cookie_session_id[3:]).hex}"
    except ValueError:
        g.session_id = f"v2-{uuid4().hex}"
    g.set_session_cookie = cookie_session_id != g.session_id


@app.after_request
def set_session_cookie(response):
    # Persist newly assigned IDs without exposing them to client-side scripts.
    if getattr(g, "set_session_cookie", False):
        # Renew only when a new or upgraded session ID was assigned.
        response.set_cookie(
            "session_id",
            g.session_id,
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            secure=request.is_secure,
            samesite="Lax",
        )
    return response


def get_data(item):
    # Find a product record by its URL slug.
    x = item.split('_')
    if x[0] == 'hoodie':
        for y in hoodie:
            if y[2] == item:
                data = y
    elif x[0] == 'tshirt':
        for y in tshirt:
            if y[2] == item:
                data = y
    return data

@app.route("/hello")
def hello_world():
    # Provide a simple JSON endpoint for checking that the application responds.
    return jsonify("hello, world!")

@app.route("/about")
def about():
    # Render the store's informational page.
    return render_template("about.html")

@app.route("/")
def index():
    # Render the store landing page.
    return render_template("home.html")


@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    # Build a Stripe Checkout session from the current browser's basket.
    line_items = [
        {"price": price_id, "quantity": quantity}
        for quantity, price_id in retrieveprice(g.session_id)
    ]
    # An empty basket cannot produce a Stripe checkout session.
    if not line_items:
        return redirect(url_for("viewbasket"))

    checkout_session = stripe.checkout.Session.create(
        line_items=line_items,
        mode="payment",
        # Stripe substitutes the Checkout Session ID after payment completes.
        success_url=YOUR_DOMAIN + "/success/{CHECKOUT_SESSION_ID}",
        cancel_url=YOUR_DOMAIN + "/cancel",
        automatic_tax={"enabled": True},
        billing_address_collection="required",
        # Bind the payment to this basket owner for verification on return.
        client_reference_id=g.session_id,
        api_key=required_setting("STRIPE_SECRET_KEY"),
    )

    return redirect(checkout_session.url, code=303)

@app.route('/success/<stripe_id>', methods=['GET'])
def success(stripe_id):
    # Verify a completed payment, then send one order notification.
    session = stripe.checkout.Session.retrieve(
        stripe_id,
        api_key=required_setting("STRIPE_SECRET_KEY"),
    )
    if session.client_reference_id != g.session_id or session.payment_status != "paid":
        abort(404)

    # Claiming prevents duplicate confirmations; failures release the claim for retry.
    if claim_order_notification(stripe_id):
        try:
            required_setting("MAIL_USERNAME")
            required_setting("MAIL_PASSWORD")
            order = retrieve(g.session_id)
            # Include the Stripe dashboard payment link for order follow-up.
            payment_link = (
                f"https://dashboard.stripe.com/payments/{session.payment_intent}"
            )
            msg = Message(
                "Order Confirmation",
                sender=required_setting("MAIL_DEFAULT_SENDER"),
                recipients=[required_setting("ORDER_NOTIFICATION_EMAIL")],
            )
            msg.body = f"{order}\n{payment_link}"
            mail.send(msg)
            complete_order_notification(stripe_id)
        except Exception:
            release_order_notification(stripe_id)
            raise
    return render_template('success.html')
    
    

@app.route("/config")
def get_publishable_key():
    # Expose the public Stripe key required by the browser checkout UI.
    return jsonify({"publicKey": required_setting("STRIPE_PUBLISHABLE_KEY")})

def setprice(total):
    # Legacy helper that stores a displayed total in a cookie before redirecting.
    resp = redirect(url_for('charge'))
    resp.set_cookie('price', str(total))
    return resp


    

@app.route('/products/<item>', methods=['GET','POST'])
def product(item):
    # Render the product details page for the selected catalog item.
    data = get_data(item)
    return render_template('productpage.html', data=data)


@app.route('/basket/<item>', methods=['GET','POST'])
def basket(item):
    # Add the selected product variant to the current browser's basket.
    quantity = request.form.get('quantity')
    size = request.form.get('size')
    x = get_data(item)
    price = x[1]
    item = item.split('_')
    add_basket(x[3], item[0], item[1], size, quantity, g.session_id, price)
    return redirect(url_for('products'))


@app.route('/products')
def products():
    # Render the catalog with both product categories.
    return render_template('products.html', tshirts=tshirt, hoodies=hoodie)

@app.route('/basket')
def viewbasket():
    # Render only the basket associated with the current session ID.
    return render_template("basket.html", data=retrieve(g.session_id))

if __name__ == "__main__":
    # Run Flask's development server when this module is executed directly.
    app.run()
