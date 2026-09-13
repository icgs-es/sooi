"""Small transport primitive for configured transactional email delivery."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def send_transactional_email(*, subject, text_body, html_body, recipient):
    """Send one multipart email using Django's configured email backend."""
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(html_body, "text/html")
    return message.send(fail_silently=False)
