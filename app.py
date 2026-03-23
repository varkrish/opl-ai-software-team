from flask import Flask
import time

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health_check():
    uptime = time.time() - start_time
    status = {
        'status': 'ok',
        'uptime': f'{uptime:.2f} seconds'
    }

    # Simulate dependency checks
    dependencies = {
        'database': {'status': 'ok', 'message': 'Database connection established'},
        'cache': {'status': 'ok', 'message': 'Cache is responsive'},
        'api_gateway': {'status': 'ok', 'message': 'Connected to API gateway'}
    }

    for name, check in dependencies.items():
        try:
            # Simulate a check
            pass
        except Exception as e:
            check['status'] = 'error'
            check['message'] = str(e)

    status['dependencies'] = dependencies
    return status

@app.route('/', methods=['GET'])
def index():
    return 'Health Check Service is running'

if __name__ == '__main__':
    start_time = time.time()
    app.run(debug=False, port=5000)
