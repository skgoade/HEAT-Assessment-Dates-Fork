import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
from datetime import datetime
import logging
# from dotenv import load_dotenv  # Add this line

# # Load environment variables from .env file
# load_dotenv()  # Add this line

app = Flask(__name__)

# Enable CORS for your frontend domain
CORS(app, resources={
    r"/api/*": {
        "origins": ["*"],  # Change to your specific domain in production
        "methods": ["POST", "GET", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration from environment variables
DB_CONFIG = {
    'host': os.environ.get('DB_HOST'),
    'user': os.environ.get('DB_USER'),
    'password': os.environ.get('DB_PASS'),
    'database': os.environ.get('DB_NAME_PROD', 'PlayerDev'),
    'port': int(os.environ.get('DB_PORT', 3306)),
    'connect_timeout': 10,
    'use_pure': True
}

def get_db_connection():
    """Create and return a database connection"""
    try:
        # Connect with the database already specified
        connection = mysql.connector.connect(**DB_CONFIG)
        logger.info("Database connection established")
        return connection
    except Exception as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise

def validate_assessment_data(data):
    """Validate incoming hitting assessment form data"""
    required_fields = ['playerName', 'assessmentDate']
    
    # Check required fields
    for field in required_fields:
        if field not in data or data[field] == '':
            return False, f"Missing required field: {field}"
    
    # Validate player name
    if len(data['playerName'].strip()) < 2:
        return False, "Player name must be at least 2 characters"
    
    # Validate assessment date format
    try:
        datetime.strptime(data['assessmentDate'], '%Y-%m-%d')
    except ValueError:
        return False, "Invalid date format. Use YYYY-MM-DD"
    
    # Validate trainer name if provided
    if 'trainerName' in data and data['trainerName']:
        if len(data['trainerName'].strip()) < 2:
            return False, "Trainer name must be at least 2 characters"
    
    return True, None

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Cloud Run"""
    try:
        # Test database connection
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503

@app.route('/api/hitting-assessment', methods=['POST', 'OPTIONS'])
def submit_assessment():
    """Handle hitting assessment submissions"""
    
    # Handle preflight request
    if request.method == 'OPTIONS':
        return '', 204
    
    try:
        # Get JSON data from request
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Validate data
        is_valid, error_message = validate_assessment_data(data)
        if not is_valid:
            logger.warning(f"Validation failed: {error_message}")
            return jsonify({"error": error_message}), 400
        
        # Connect to database
        connection = get_db_connection()
        
        try:
            with connection.cursor() as cursor:
                # Prepare SQL query
                sql = """
                    INSERT INTO hitting_assessments 
                    (assessment_date, player_name, trainer_name, notes)
                    VALUES (%s, %s, %s, %s)
                """
                
                # Prepare values
                values = (
                    data['assessmentDate'],
                    data['playerName'].strip(),
                    data.get('trainerName', '').strip() or None,
                    data.get('notes', '').strip() or None
                )
                
                # Execute query
                cursor.execute(sql, values)
                connection.commit()
                
                # Get the auto-incremented assessment_id
                assessment_id = cursor.lastrowid
                
                logger.info(f"Assessment saved for player: {data['playerName']}, ID: {assessment_id}")
                
                return jsonify({
                    "success": True,
                    "message": "Hitting assessment submitted successfully",
                    "assessment_id": assessment_id,
                    "player": data['playerName'],
                    "assessment_date": data['assessmentDate']
                }), 201
                
        finally:
            connection.close()
            
    except mysql.connector.Error as e:
        logger.error(f"Database error: {str(e)}")
        return jsonify({"error": "Database error occurred"}), 500
        
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/api/hitting-assessment/<int:assessment_id>', methods=['GET'])
def get_assessment(assessment_id):
    """Retrieve a specific hitting assessment by ID"""
    try:
        connection = get_db_connection()
        
        try:
            with connection.cursor(dictionary=True) as cursor:
                sql = """
                    SELECT 
                        assessment_id,
                        assessment_date,
                        player_name,
                        trainer_name,
                        notes,
                        created_at,
                        updated_at
                    FROM hitting_assessments
                    WHERE assessment_id = %s
                """
                
                cursor.execute(sql, (assessment_id,))
                result = cursor.fetchone()
                
                if not result:
                    return jsonify({"error": "Assessment not found"}), 404
                
                # Convert date objects to strings for JSON serialization
                if result['assessment_date']:
                    result['assessment_date'] = result['assessment_date'].strftime('%Y-%m-%d')
                if result['created_at']:
                    result['created_at'] = result['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if result['updated_at']:
                    result['updated_at'] = result['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                return jsonify(result), 200
                
        finally:
            connection.close()
            
    except Exception as e:
        logger.error(f"Error retrieving assessment: {str(e)}")
        return jsonify({"error": "An error occurred"}), 500

@app.route('/api/hitting-assessment/player/<player_name>', methods=['GET'])
def get_player_assessments(player_name):
    """Retrieve all assessments for a specific player"""
    try:
        connection = get_db_connection()
        
        try:
            with connection.cursor(dictionary=True) as cursor:
                sql = """
                    SELECT 
                        assessment_id,
                        assessment_date,
                        player_name,
                        trainer_name,
                        notes,
                        created_at
                    FROM hitting_assessments
                    WHERE player_name = %s
                    ORDER BY assessment_date DESC
                """
                
                cursor.execute(sql, (player_name,))
                results = cursor.fetchall()
                
                # Convert date objects to strings
                for result in results:
                    if result['assessment_date']:
                        result['assessment_date'] = result['assessment_date'].strftime('%Y-%m-%d')
                    if result['created_at']:
                        result['created_at'] = result['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                return jsonify({
                    "player_name": player_name,
                    "total_assessments": len(results),
                    "assessments": results
                }), 200
                
        finally:
            connection.close()
            
    except Exception as e:
        logger.error(f"Error retrieving player assessments: {str(e)}")
        return jsonify({"error": "An error occurred"}), 500

@app.route('/api/hitting-assessment/recent', methods=['GET'])
def get_recent_assessments():
    """Retrieve recent assessments (last 30 days)"""
    try:
        limit = request.args.get('limit', 50, type=int)
        
        connection = get_db_connection()
        
        try:
            with connection.cursor(dictionary=True) as cursor:
                sql = """
                    SELECT 
                        assessment_id,
                        assessment_date,
                        player_name,
                        trainer_name,
                        notes,
                        created_at
                    FROM hitting_assessments
                    WHERE assessment_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    ORDER BY assessment_date DESC, created_at DESC
                    LIMIT %s
                """
                
                cursor.execute(sql, (limit,))
                results = cursor.fetchall()
                
                # Convert date objects to strings
                for result in results:
                    if result['assessment_date']:
                        result['assessment_date'] = result['assessment_date'].strftime('%Y-%m-%d')
                    if result['created_at']:
                        result['created_at'] = result['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                
                return jsonify({
                    "total": len(results),
                    "assessments": results
                }), 200
                
        finally:
            connection.close()
            
    except Exception as e:
        logger.error(f"Error retrieving recent assessments: {str(e)}")
        return jsonify({"error": "An error occurred"}), 500

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # For local development only
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
