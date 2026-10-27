"""API responses are per-user and change on every write: tell browsers/CDNs (Cloudflare) not to cache them.
R19: GSTR-1 'filed' status and payment cards were stale until a hard reload."""


class ApiNoStoreMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/api/') and not response.has_header('Cache-Control'):
            response['Cache-Control'] = 'no-store'
        return response
