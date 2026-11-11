from fastapi import Request

async def add_security_and_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/images/") or path.endswith((".webp", ".png", ".jpg", ".jpeg", ".ico", ".svg", ".woff2")):
        # Cache static brand assets and background images for 30 days
        response.headers["Cache-Control"] = "public, max-age=2592000, stale-while-revalidate=86400"
    elif path.startswith(("/thumb/", "/preview/")):
        response.headers["Cache-Control"] = "public, max-age=604800, stale-while-revalidate=86400"

    # Security Headers for browser protection
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    return response

