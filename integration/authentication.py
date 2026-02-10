from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import ApiKey

class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        api_key = request.headers.get('X-Api-Key')
        if not api_key:
            return None

        try:
            key_obj = ApiKey.objects.get(key=api_key, is_active=True)
            # Update usage timestamp (optional, maybe async in prod)
            # key_obj.last_used_at = timezone.now()
            # key_obj.save(update_fields=['last_used_at'])
            return (key_obj.user, key_obj)
        except ApiKey.DoesNotExist:
            raise AuthenticationFailed('Invalid or inactive API Key')
