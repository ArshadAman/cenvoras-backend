from django.urls import path
from .views import PublicProductListView, PublicOrderCreateView

urlpatterns = [
    path('products/', PublicProductListView.as_view(), name='public-products'),
    path('orders/', PublicOrderCreateView.as_view(), name='public-orders'),
]
