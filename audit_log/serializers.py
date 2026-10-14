from rest_framework import serializers
from .models import AuditLog


def normalize_audit_changes(action, changes):
    """
    Normalize audit change payloads for the UI:
    - HR before/after blobs → per-field {old, new}
    - Flat UPDATE scalars → {field: {old: None, new: value}} avoided; use 'set to'
      by emitting {old: None, new: value} only when not already nested.
    """
    if not isinstance(changes, dict) or not changes:
        return changes or {}

    # Legacy HR shape
    if 'before' in changes and 'after' in changes and action == 'UPDATE':
        before = changes.get('before') if isinstance(changes.get('before'), dict) else {}
        after = changes.get('after') if isinstance(changes.get('after'), dict) else {}
        keys = set(before.keys()) | set(after.keys())
        out = {}
        for key in keys:
            if str(key).startswith('_'):
                continue
            old_v = before.get(key)
            new_v = after.get(key)
            if old_v != new_v:
                out[key] = {'old': old_v, 'new': new_v}
        return out or changes

    if action == 'UPDATE':
        out = {}
        for key, val in changes.items():
            if str(key).startswith('_'):
                continue
            if isinstance(val, dict) and ('old' in val or 'new' in val):
                out[key] = val
            else:
                # Flat scalar from signal fallback — do not invent "None →" via FE
                out[key] = {'old': None, 'new': val, 'set_only': True}
        return out

    return changes


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.username', read_only=True)
    changes = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = '__all__'

    def get_changes(self, obj):
        return normalize_audit_changes(obj.action, obj.changes or {})
