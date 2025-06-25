from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash
from models.models import User
from models.database import db

auth_bp = Blueprint("auth", __name__)  # Usa la carpeta global de templates

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session["user_id"] = user.id
            session["is_admin"] = user.is_admin
            flash("Inicio de sesión exitoso.", "success")
            if user.is_admin:
                return redirect(url_for("admin.dashboard"))
            else:
                # Ahora apuntamos directamente al dashboard de time.py
                return redirect(url_for("time.dashboard_employee"))
        else:
            flash("Nombre de usuario o contraseña incorrectos.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("is_admin", None)
    flash("Has cerrado sesión.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/registro", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username         = request.form.get("username")
        full_name        = request.form.get("full_name")
        email            = request.form.get("email")
        password         = request.form.get("password")
        confirm_password = request.form.get("confirm_password")

        if password != confirm_password:
            flash("Las contraseñas no coinciden.", "danger")
            return redirect(url_for("auth.register"))
        if User.query.filter_by(username=username).first():
            flash("El nombre de usuario ya existe.", "danger")
            return redirect(url_for("auth.register"))
        if User.query.filter_by(email=email).first():
            flash("El email ya está registrado.", "danger")
            return redirect(url_for("auth.register"))

        nuevo_usuario = User(
            username=username,
            full_name=full_name,
            email=email,
            is_admin=False
        )
        nuevo_usuario.set_password(password)
        db.session.add(nuevo_usuario)
        db.session.commit()

        flash("Registro exitoso. Ya puedes iniciar sesión.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")
