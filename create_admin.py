import os
from aggregator import create_app, db
from aggregator.models import ROLE_ADMIN, User
import sys

def create_admin(username, email, password):
    app = create_app()
    with app.app_context():
        # Check if user already exists
        existing_user = User.query.filter((User.username == username) | (User.email == email)).first()
        if existing_user:
            print(f"User {username} or email {email} already exists.")
            return

        user = User(username=username, email=email)
        user.set_role(ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin user {username} created successfully.")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python create_admin.py <username> <email> <password>")
    else:
        create_admin(sys.argv[1], sys.argv[2], sys.argv[3])
