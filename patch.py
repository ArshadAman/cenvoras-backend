import re
with open('hr/serializers.py', 'r') as f:
    content = f.read()

new_create = """
    def create(self, validated_data):
        components_data = validated_data.pop('components', [])
        import traceback
        try:
            structure = super().create(validated_data)
        except Exception as e:
            print("DB ERROR IN CREATE:", e)
            traceback.print_exc()
            raise e
        for comp in components_data:
            SalaryComponent.objects.create(salary_structure=structure, **comp)
        return structure
"""

content = re.sub(r'    def create\(self, validated_data\):.*?return structure', new_create.strip('\n'), content, flags=re.DOTALL)
with open('hr/serializers.py', 'w') as f:
    f.write(content)
