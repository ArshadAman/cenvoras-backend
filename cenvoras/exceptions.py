import logging

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """Return safe API errors and avoid leaking internal exception details."""
    response = exception_handler(exc, context)
    if response is not None:
        return response

    view_name = getattr(context.get('view'), '__class__', type('UnknownView', (), {})).__name__
    request = context.get('request')
    path = getattr(request, 'path', 'unknown-path')
    logger.exception('Unhandled API exception in %s at %s', view_name, path)

    message = 'An unexpected error occurred. Please try again later.'
    if settings.DEBUG:
        return Response(
            {
                'detail': message,
                'error_type': exc.__class__.__name__,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response({'detail': message}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)