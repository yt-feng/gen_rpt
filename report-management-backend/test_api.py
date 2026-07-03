
import httpx
import json

base_url = 'http://127.0.0.1:8000/api/v1'
headers = {'Authorization': 'Bearer placeholder-token'}

resp = httpx.get(f'{base_url}/reports/', headers=headers)
print('GET /reports/:', resp.status_code)
reports = resp.json().get('data', [])
for r in reports:
    print(' -', r.get('id'), ':', r.get('title'))

doc_id = 'neet-ug-2026-exam-retests-and-the-rise-of-local-tech-unicorn-887edd'
resp2 = httpx.get(f'{base_url}/reports/{doc_id}', headers=headers)
print(f'GET /reports/{doc_id}:', resp2.status_code)
report_details = resp2.json().get('data', {})
print('Title:', report_details.get('title'))
sections = report_details.get('reportContent', {}).get('sections', [])
print(f'Found {len(sections)} sections')
if sections:
    print('First section heading:', sections[0].get('heading'))

