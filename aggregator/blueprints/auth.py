from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, current_user
from aggregator.models import User
from urllib.parse import urlparse

auth = Blueprint("auth", __name__)

@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.list_articles"))
    
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = True if request.form.get("remember") else False
        
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            if not next_page or urlparse(next_page).netloc != "":
                next_page = url_for("admin.list_articles")
            return redirect(next_page)
        else:
            flash("Invalid username or password")
            
    return render_template("login.html")

@auth.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("public.headlines"))
