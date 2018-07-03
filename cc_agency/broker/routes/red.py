from flask import jsonify


def red_routes(app):
    @app.route('/red', methods=['POST'])
    def post_red():
        return jsonify({'Hello': 'World'})
