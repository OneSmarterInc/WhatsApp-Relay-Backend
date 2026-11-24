from django.conf import settings
from django.http import JsonResponse


class PrivateResponsesMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            try:
                size = int(request.META.get('CONTENT_LENGTH') or 0)
            except ValueError:
                return JsonResponse({'detail': 'Invalid request size.'}, status=400)
            if size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
                return JsonResponse({'detail': 'Request exceeds the configured upload limit.'}, status=413)
        response = self.get_response(request)
        if request.path.startswith('/api/'):
            response['Cache-Control'] = 'private, no-store, max-age=0'
            response['Vary'] = 'Cookie'
            response['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'"
        return response
