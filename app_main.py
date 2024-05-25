from flask import Flask, redirect, url_for, request, jsonify, render_template
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
import stripe, uuid
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph , Image
from io import BytesIO

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:azeem@localhost:5432/datamanaging1'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USERNAME'] = 'mohamedazeems069@gmail.com'
app.config['MAIL_PASSWORD'] = 'mnlxquubpwepqfxo'
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USE_SSL'] = True
mail = Mail(app)


stripe.api_key = "sk_test_51P9hmVSEEqXDiDF9SqS2mLik6d5emBflRIcwaDmzXm0maFXdey0hwNda8YPJl5NRlQzGgf8xjYhuDGXvG6Q9wTmq00qGB3Bgkp"

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100))
    currency = db.Column(db.String(10))
    amount = db.Column(db.Integer)
    success = db.Column(db.Boolean)
    customer_email = db.Column(db.String(100))
    receipt_number = db.Column(db.String(100))
    subscription_type = db.Column(db.String(50))  # New field for subscription type (e.g., 'monthly', 'yearly')
    subscription_start_date = db.Column(db.DateTime, default=datetime.utcnow)  # New field for start date
    subscription_end_date = db.Column(db.DateTime)  # New field for end date
    subscription_status = db.Column(db.String(20), default='subscribed')
    alert_sent = db.Column(db.Boolean, default=False)  # New field to track alert status

    def _repr_(self):
        return f'<Transaction {self.id}: {self.product_name}, {self.receipt_number}, {self.subscription_type}>'

    def calculate_subscription_end_date(self):
        if self.subscription_type == 'yearly':
            self.subscription_end_date = self.subscription_start_date + timedelta(days=3)#365
        elif self.subscription_type == 'monthly':
            self.subscription_end_date = self.subscription_start_date + timedelta(days=3)#30
        else:
            raise ValueError("Invalid subscription type")

        return self.subscription_end_date


class CardDetails(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cardholder_name = db.Column(db.String(100))
    card_number = db.Column(db.String(20))  # Assuming the card number will be stored as a string
    expiration_month = db.Column(db.Integer)
    expiration_year = db.Column(db.Integer)
    cvc = db.Column(db.String(4))  # Card Verification Code
    customer_email = db.Column(db.String(100))

    def _repr_(self):
        return f'<CardDetails {self.id}: {self.cardholder_name}, {self.card_number}>'


with app.app_context():
    db.create_all()


def send_email(transaction):
    msg = Message("Payment Successful",
                  sender="mohamedazeems069@gmail.com",
                  recipients=[transaction.customer_email])
    msg.body = f"Your payment for {transaction.product_name} has been processed successfully. Receipt No: {transaction.receipt_number}"
    mail.send(msg)


def send_alert_email(transaction, days_left):
    msg = Message("Subscription Ending Soon",
                  sender="mohamedazeems069@gmail.com",
                  recipients=[transaction.customer_email])
    msg.body = f"Dear Customer, your subscription for {transaction.product_name} will end in {days_left} days on {transaction.subscription_end_date.strftime('%Y-%m-%d')}. Please renew your subscription to continue enjoying our services."
    mail.send(msg)


@app.route("/")
def index():
    return render_template("checkout.html")


@app.route("/checkout", methods=["GET"])
def show_checkout_form():
    return render_template("checkout.html")


@app.route("/checkout", methods=["POST"])
def checkout():
    # Extract data from form fields
    customer_email = request.form.get("customer_email")
    subscription_type = request.form.get("subscription_type")  # 'monthly' or 'yearly'

    if not customer_email:
        return jsonify({"error": "Email not provided"}), 400
    if not subscription_type or subscription_type not in ['monthly', 'yearly']:
        return jsonify({"error": "Invalid or no subscription type provided"}), 400

    # Define line item based on the subscription type
    if subscription_type == 'monthly':
        price_id = 'price_1PJY6dSEEqXDiDF9MgE2oBvg'
    else:
        price_id = 'price_1PJdyiSEEqXDiDF9s2lnn2uU'

    try:
        # Retrieve price information from Stripe
        price = stripe.Price.retrieve(price_id)
        amount = price.unit_amount_decimal
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    line_item = {
        'price': price_id,
        'quantity': 1
    }

    receipt_number = str(uuid.uuid4())
    subscription_start_date = datetime.utcnow()

    new_transaction = Transaction(
        product_name=f"{'Monthly' if subscription_type == 'monthly' else 'Yearly'} Subscription",
        currency='inr',
        amount=amount,  # Since price is now fetched from Stripe, set amount to None
        success=False,
        customer_email=customer_email,
        receipt_number=receipt_number,
        subscription_type=subscription_type,
        subscription_start_date=subscription_start_date
    )
    new_transaction.calculate_subscription_end_date()
    new_transaction.subscription_status = 'subscribed'
    try:
        session = stripe.checkout.Session.create(
            customer_email=customer_email,
            billing_address_collection='auto',
            payment_method_types=['card'],
            line_items=[line_item],
            mode='subscription',
            success_url=url_for("payment_success", receipt_number=receipt_number, _external=True),
            cancel_url=url_for("payment_failure", _external=True)
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Assign Stripe subscription ID to the transaction
    new_transaction.stripe_subscription_id = session.subscription

    db.session.add(new_transaction)
    db.session.commit()

    return redirect(session.url)


# Cancel Subscription API
@app.route("/subscription/cancel", methods=["POST"])
def cancel_subscription():
    if not request.is_json:
        return jsonify({"error": "Invalid content type. Please use 'Content-Type: application/json'"}), 400

    data = request.get_json()
    customer_email = data.get("customer_email")

    if not customer_email:
        return jsonify({"error": "Email not provided"}), 400

    latest_transaction = Transaction.query.filter_by(customer_email=customer_email).order_by(
        Transaction.subscription_start_date.desc()).first()

    if latest_transaction:
        if latest_transaction.success:
            try:
                # Call Stripe API to cancel subscription
                # Here you need to implement Stripe API to cancel the subscription
                # stripe.Subscription.delete(...)

                latest_transaction.subscription_status = 'cancelled'  # Update subscription status to "cancelled"
                db.session.delete(latest_transaction)
                db.session.commit()

                # Send email notification
                send_cancelled_subscription_email(latest_transaction)

                return jsonify({"message": "Subscription cancelled successfully"}), 200
            except Exception as e:
                db.session.rollback()
                return jsonify({"error": str(e)}), 500
        else:
            return jsonify({"error": "Subscription cannot be cancelled as payment was not successful"}), 400
    else:
        return jsonify({"error": "No subscription found for the provided email"}), 404


def send_cancelled_subscription_email(transaction):
    msg = Message("Subscription Cancelled",
                  sender="mohamedazeems069@gmail.com",
                  recipients=[transaction.customer_email])
    msg.body = f"Your subscription for {transaction.product_name} has been cancelled. We hope to see you again soon!"
    mail.send(msg)


# Define the generate_invoice_pdf function to create the PDF invoice
def generate_invoice_pdf(transaction):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    style = styles["Normal"]
    # Add your logo image file path here
    logo_path = r"C:\Users\AZEEM\Downloads\WhatsApp Image 2024-05-24 at 3.39.15 PM.jpeg"

    # Insert the logo image
    logo = Image(logo_path, width=100, height=100)  # Adjust width and height as needed
    content = [logo]
    content.extend ( [
        Paragraph(f"Receipt Number: {transaction.receipt_number}", style),
        Paragraph(f"Product Name: {transaction.product_name}", style),
        Paragraph(f"Amount: {transaction.amount} {transaction.currency}", style),
        # Add more transaction details as needed
    ])
    doc.build(content)
    buffer.seek(0)
    return buffer


# Define the send_email_with_invoice function to send the email with the attached PDF invoice
def send_email_with_invoice(transaction, invoice_file):
    msg = Message("Payment Successful with Invoice",
                  sender="mohamedazeems069@gmail.com",
                  recipients=[transaction.customer_email])
    msg.body = f"Your payment for {transaction.product_name} has been processed successfully. Receipt No: {transaction.receipt_number}"
    msg.attach(filename="invoice.pdf", content_type="application/pdf", data=invoice_file.read())
    mail.send(msg)


# Define the /payment/success route
@app.route("/payment/success")
def payment_success():
    receipt_number = request.args.get('receipt_number')
    transaction = Transaction.query.filter_by(receipt_number=receipt_number).first()

    if transaction:
        transaction.success = True
        db.session.commit()

        # Generate and attach PDF invoice
        invoice_file = generate_invoice_pdf(transaction)

        # Send email with attached PDF invoice
        send_email_with_invoice(transaction, invoice_file)

        # Optionally, mark alert as sent
        # transaction.alert_sent = True
        # db.session.commit()
        # check_and_send_alerts(transaction)

    return render_template("success.html")





@app.route("/payment/failure")
def payment_failure():
    return "Payment was cancelled or failed.", 200


# Get UI Payment datas
@app.route("/payments", methods=["GET"])
def get_payments():
    payment_intent_id = request.args.get('payment_intent_id')
    fetch_all = request.args.get('all') == 'true'

    if payment_intent_id:
        # Fetch a specific payment intent
        try:
            payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return jsonify(payment_intent), 200
        except stripe.error.StripeError as e:
            return jsonify(error=str(e)), 400

    elif fetch_all:
        # Fetch all payment intents with a reasonable limit
        try:
            all_payments = stripe.PaymentIntent.list(limit=100)  # Limit can be adjusted based on your needs
            return jsonify([p for p in all_payments.auto_paging_iter()]), 200
        except stripe.error.StripeError as e:
            return jsonify(error=str(e)), 400

    return jsonify({"error": "Please provide a Payment Intent ID, a Customer Email, or set 'all' to true"}), 400


# GET_BY_Receipt_number from db
@app.route("/transactions", methods=["GET"])
def get_transactions():
    customer_email = request.args.get("customer_email")
    receipt_number = request.args.get("receipt_number")

    query = Transaction.query

    if customer_email:
        query = query.filter(Transaction.customer_email == customer_email)
    if receipt_number:
        query = query.filter(Transaction.receipt_number == receipt_number)

    transactions = query.all()
    transactions_data = [{
        "product_name": transaction.product_name,
        "currency": transaction.currency,
        "amount": transaction.amount,
        "success": transaction.success,
        "customer_email": transaction.customer_email,
        "receipt_number": transaction.receipt_number
    } for transaction in transactions]

    return jsonify(transactions_data)


# GET ALL from db
@app.route("/transactions", methods=["GET"])
def get_alltransactions():
    transactions = Transaction.query.all()

    transactions_data = [{
        "product_name": transaction.product_name,
        "currency": transaction.currency,
        "amount": transaction.amount,
        "success": transaction.success,
        "customer_email": transaction.customer_email,
        "receipt_number": transaction.receipt_number
    } for transaction in transactions]

    return jsonify(transactions_data)


def check_and_send_alerts():
    today = datetime.utcnow().date()
    transactions = Transaction.query.filter(
        Transaction.subscription_end_date <= today + timedelta(days=3),
        Transaction.subscription_end_date >= today,
        Transaction.alert_sent == False,
        Transaction.success == True
    ).all()

    for transaction in transactions:
        days_left = (transaction.subscription_end_date.date() - today).days
        send_alert_email(transaction, days_left)
        if days_left <= 3:
            transaction.alert_sent = True
            db.session.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_and_send_alerts, trigger="interval", hours=24)



@app.route("/send-alert", methods=["POST"])
def send_alert():
    data = request.get_json()
    receipt_number = data.get("receipt_number")

    if not receipt_number:
        return jsonify({"error": "Receipt number not provided"}), 400

    transaction = Transaction.query.filter_by(receipt_number=receipt_number, alert_sent=False, success=True).first()

    if transaction:
        today = datetime.utcnow().date()
        days_left = (transaction.subscription_end_date.date() - today).days
        if days_left <= 3:
            try:
                send_alert_email(transaction, days_left)
                transaction.alert_sent = True
                db.session.commit()
                return jsonify({"message": "Alert sent successfully"}), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        else:
            return jsonify({"message": "Subscription end date is more than 3 days away"}), 400
    else:
        return jsonify({"error": "No transaction found or alert already sent"}), 404




if __name__ == "__main__":
    scheduler.start()
    app.run(port=4242, debug=True)