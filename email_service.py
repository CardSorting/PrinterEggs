import os
import requests
from flask import url_for
from datetime import datetime
from typing import Dict, Optional


class EmailService:
    def __init__(self):
        self.sendlayer_api_key = os.getenv('SENDLAYER_API_KEY')
        self.sender_email = 'noreply@yourdomain.com'
        self.sender_name = 'YourApp Security Team'
        self.sendlayer_api_url = "https://console.sendlayer.com/api/v1/email"

    def send_verification_email(self, email: str, token: str) -> None:
        """Send a verification email to the user with a unique verification link."""
        verification_url = url_for('auth.verify_email', token=token, _external=True)
        self._send_email(
            email=email,
            subject=f"Verify Your Email Address for {self._app_name()}",
            html_content=self._generate_email_content(verification_url, action='Verify Your Email Address')
        )

    def send_reset_email(self, email: str, token: str) -> None:
        """Send a password reset email to the user with a unique reset link."""
        reset_url = url_for('auth.reset_password', token=token, _external=True)
        self._send_email(
            email=email,
            subject=f"Reset Your Password for {self._app_name()}",
            html_content=self._generate_email_content(reset_url, action='Reset Your Password')
        )

    def _send_email(self, email: str, subject: str, html_content: str) -> None:
        """Internal method to handle sending an email via the SendLayer API."""
        headers = self._prepare_email_headers()
        data = self._prepare_email_data(email, subject, html_content)
        try:
            response = requests.post(self.sendlayer_api_url, headers=headers, json=data)
            response.raise_for_status()
            print(f"Email sent to {email}.")
        except requests.exceptions.RequestException as e:
            print(f"Failed to send email to {email}: {e}")

    def _prepare_email_headers(self) -> Dict[str, str]:
        """Prepare headers required for the SendLayer API request."""
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.sendlayer_api_key}'
        }

    def _prepare_email_data(self, email: str, subject: str, html_content: str) -> Dict[str, any]:
        """Prepare the email payload for the SendLayer API."""
        return {
            "From": {"name": self.sender_name, "email": self.sender_email},
            "To": [{"name": email.split('@')[0], "email": email}],
            "Subject": subject,
            "ContentType": "HTML",
            "HTMLContent": html_content
        }

    def _generate_email_content(self, action_url: str, action: str) -> str:
        """Generate the HTML content for the email based on the action type."""
        app_name = self._app_name()
        year = datetime.now().year
        return f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{action}</title>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #4CAF50; color: white; text-align: center; padding: 10px; }}
                .content {{ background-color: #f9f9f9; border: 1px solid #ddd; padding: 20px; }}
                .button {{ display: inline-block; padding: 10px 20px; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 5px; }}
                .footer {{ text-align: center; margin-top: 20px; font-size: 0.8em; color: #777; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{self.sender_name}</h1>
                </div>
                <div class="content">
                    <h2>{action}</h2>
                    <p>To ensure the security of your account, please {action.lower()} by clicking the button below:</p>
                    <p style="text-align: center;">
                        <a href="{action_url}" class="button">{action.split()[0]}</a>
                    </p>
                    <p>If you didn't request this, please ignore this email or contact our support team if you have concerns.</p>
                    <p>For your security:</p>
                    <ul>
                        <li>We will never ask for your password via email.</li>
                        <li>Always check that emails from us use our official domain: {self.sender_email.split('@')[1]}</li>
                        <li>If you're unsure about an email's authenticity, please contact our support team.</li>
                    </ul>
                </div>
                <div class="footer">
                    <p>This is an automated message, please do not reply to this email. If you need assistance, please contact our support team.</p>
                    <p>&copy; {year} {app_name}. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """

    def _app_name(self) -> str:
        """Helper method to extract the app name from the sender's name."""
        return self.sender_name.split()[0]