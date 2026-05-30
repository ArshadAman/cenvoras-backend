import requests

login_data = {"username": "arshadaman202@gmail.com", "password": "arshad@001"}
res = requests.post("http://localhost:8000/api/users/login/", json=login_data)
token = res.json().get("access")

headers = {"Authorization": f"Bearer {token}"}
emp_data = {
    "full_name": "Test User",
    "personal_email": "testuser99@cenvora.app",
    "date_of_birth": "1990-01-01",
    "gender": "male",
    "employment_type": "full_time",
    "work_state": "MH",
    "date_of_joining": "2023-01-01",
    "department": None,
    "designation": None
}

# Fetch dept and desig to assign
dept_res = requests.get("http://localhost:8000/api/hr/departments/", headers=headers).json()
if dept_res.get('results'): emp_data["department"] = dept_res['results'][0]['id']

desig_res = requests.get("http://localhost:8000/api/hr/designations/", headers=headers).json()
if desig_res.get('results'): emp_data["designation"] = desig_res['results'][0]['id']

create_res = requests.post("http://localhost:8000/api/hr/employees/", json=emp_data, headers=headers)
print("CREATE STATUS:", create_res.status_code)
print("CREATE BODY:", create_res.text)
