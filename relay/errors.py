from rest_framework.views import exception_handler
from rest_framework.response import Response
from django.db import OperationalError


def api_exception_handler(exc, context):
    if isinstance(exc, OperationalError):
        return Response({'detail': 'Database temporarily unavailable. Restart the Relay backend if this persists, then retry. Your saved chats are retained.'}, status=503, headers={'Retry-After': '5'})
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and 'detail' not in response.data:
        response.data = {'detail': 'Please check the submitted fields.', 'fields': response.data}
    return response
