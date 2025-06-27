@admin_bp.route("/calendar")
@admin_required
def calendar():
    users = User.query.order_by(User.username).all()
    categories = ['Cocina', 'Delivery', 'Reparto', 'Sala']

    selected_user_id = request.args.get("user_id", type=int)
    selected_category = request.args.get("category")
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    start_date = (
        datetime.strptime(start_date_str, "%Y-%m-%d").date()
        if start_date_str else None
    )
    end_date = (
        datetime.strptime(end_date_str, "%Y-%m-%d").date()
        if end_date_str else None
    )

    query = TimeRecord.query.join(User, TimeRecord.user_id == User.id)

    if selected_user_id:
        query = query.filter(User.id == selected_user_id)
    if selected_category:
        query = query.filter(User.category == selected_category)
    if start_date:
        query = query.filter(TimeRecord.date >= start_date)
    if end_date:
        query = query.filter(TimeRecord.date <= end_date)

    records = query.order_by(TimeRecord.date).all()

    def color_por_categoria(cat):
        return {
            "Cocina": "#facc15",    # amarillo
            "Delivery": "#60a5fa",  # azul
            "Reparto": "#86efac",   # verde
            "Sala": "#f472b6",      # rosa
        }.get(cat, "#cbd5e1")       # gris por defecto

    events = []
    for rec in records:
        username = rec.user.username
        categoria = rec.user.category or ""
        color = color_por_categoria(categoria)
        if rec.check_in:
            events.append({
                "title": f"{username} entrada",
                "start": rec.check_in.isoformat(),
                "color": color
            })
        if rec.check_out:
            events.append({
                "title": f"{username} salida",
                "start": rec.check_out.isoformat(),
                "color": color
            })

    return render_template(
        "admin.calendar.html",
        users=users,
        categories=categories,
        selected_user_id=selected_user_id,
        selected_category=selected_category,
        start_date=start_date_str or "",
        end_date=end_date_str or "",
        events=events,
    )
