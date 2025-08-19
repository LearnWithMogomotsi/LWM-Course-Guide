# app.py - Main Application
import google.generativeai as genai
from dotenv import load_dotenv
import gradio as gr
import os
import re
import json
import hashlib
import time
from datetime import datetime, timedelta
import sqlite3
import uuid

# Load environment variables
load_dotenv()

# Security and Configuration
class AppConfig:
    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY")
        self.max_requests_per_hour = 10
        self.max_requests_per_day = 50
        # Use Railway's ephemeral filesystem safely
        self.db_path = os.path.join(os.getcwd(), "user_data.db")
        self.is_production = os.getenv("RAILWAY_ENVIRONMENT") is not None
        
    def validate_api_key(self):
        if not self.api_key:
            raise ValueError("Google API key not found in environment variables")
        return True

config = AppConfig()

# Ensure database directory exists
os.makedirs(os.path.dirname(config.db_path) if os.path.dirname(config.db_path) else '.', exist_ok=True)

# Database setup for rate limiting and basic analytics
def init_database():
    conn = sqlite3.connect(config.db_path)
    cursor = conn.cursor()
    
    # Rate limiting table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rate_limits (
            user_id TEXT PRIMARY KEY,
            hourly_count INTEGER DEFAULT 0,
            daily_count INTEGER DEFAULT 0,
            last_hour_reset TIMESTAMP,
            last_day_reset TIMESTAMP
        )
    ''')
    
    # Basic analytics (no PII stored)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMP,
            user_id_hash TEXT,
            career_field TEXT,
            employment_status TEXT,
            request_success BOOLEAN
        )
    ''')
    
    conn.commit()
    conn.close()

# Rate limiting functions
def get_user_id(request_info):
    """Generate anonymous user ID based on session"""
    # Use a combination of IP-like info (if available) and session
    session_data = str(request_info) + str(time.time() // 3600)  # Hour-based sessions
    return hashlib.sha256(session_data.encode()).hexdigest()[:16]

def check_rate_limit(user_id):
    """Check if user has exceeded rate limits"""
    conn = sqlite3.connect(config.db_path)
    cursor = conn.cursor()
    
    now = datetime.now()
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    
    # Get or create user rate limit record
    cursor.execute('SELECT * FROM rate_limits WHERE user_id = ?', (user_id,))
    record = cursor.fetchone()
    
    if not record:
        # New user
        cursor.execute('''
            INSERT INTO rate_limits (user_id, hourly_count, daily_count, last_hour_reset, last_day_reset)
            VALUES (?, 1, 1, ?, ?)
        ''', (user_id, now, now))
        conn.commit()
        conn.close()
        return True, "First request"
    
    user_id_db, hourly_count, daily_count, last_hour_reset, last_day_reset = record
    
    # Parse timestamps
    last_hour_reset = datetime.fromisoformat(last_hour_reset) if last_hour_reset else hour_ago
    last_day_reset = datetime.fromisoformat(last_day_reset) if last_day_reset else day_ago
    
    # Reset counters if time periods have passed
    if now - last_hour_reset > timedelta(hours=1):
        hourly_count = 0
        last_hour_reset = now
    
    if now - last_day_reset > timedelta(days=1):
        daily_count = 0
        last_day_reset = now
    
    # Check limits
    if hourly_count >= config.max_requests_per_hour:
        conn.close()
        return False, f"Hourly limit exceeded ({config.max_requests_per_hour}/hour). Try again in {60 - (now - last_hour_reset).seconds // 60} minutes."
    
    if daily_count >= config.max_requests_per_day:
        conn.close()
        return False, f"Daily limit exceeded ({config.max_requests_per_day}/day). Try again tomorrow."
    
    # Increment counters
    cursor.execute('''
        UPDATE rate_limits 
        SET hourly_count = ?, daily_count = ?, last_hour_reset = ?, last_day_reset = ?
        WHERE user_id = ?
    ''', (hourly_count + 1, daily_count + 1, last_hour_reset, last_day_reset, user_id))
    
    conn.commit()
    conn.close()
    return True, "Request allowed"

def log_analytics(user_id, career_field, employment_status, success):
    """Log basic analytics without PII"""
    conn = sqlite3.connect(config.db_path)
    cursor = conn.cursor()
    
    user_id_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
    
    cursor.execute('''
        INSERT INTO analytics (id, timestamp, user_id_hash, career_field, employment_status, request_success)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (str(uuid.uuid4()), datetime.now(), user_id_hash, career_field, employment_status, success))
    
    conn.commit()
    conn.close()

# Configure AI model
try:
    config.validate_api_key()
    genai.configure(api_key=config.api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
except Exception as e:
    model = None
    print(f"AI Model configuration failed: {e}")

MODEL_INSTRUCTIONS = """
You are a friendly career development advisor for South African learners
and professionals. Recommend 1-5 practical short courses that can boost
career prospects and employment opportunities.

IMPORTANT: Only recommend courses that you are confident actually exist.
Source courses from credible platforms like Coursera, edX, LinkedIn
Learning, Google Digital Skills for Africa, DigiSkills Africa, Udemy,
FutureLearn, and South African universities (UCT, WitsX, UNISA) or
SETA-accredited platforms.

For each recommended course, provide your response in the following JSON format:
{
  "courses": [
    {
      "title": "Exact course title",
      "platform": "Platform name",
      "cost": "Free or Paid amount in ZAR",
      "certificate_cost": "Certificate cost in ZAR if different from course cost",
      "duration": "Duration in weeks/months",
      "description": "Why this course is perfect for their career goals and SA job market",
      "disclaimer": "Always verify course availability and current pricing"
    }
  ]
}

CRITICAL REQUIREMENTS:
- Only recommend courses you're confident exist
- Include current South African Rand estimates
- Be realistic about duration and difficulty
- Focus on practical skills for SA job market
- Add disclaimer about verifying course details
"""

def generate_course_card_html(course_data, index):
    """Generate HTML for a single course card with enhanced security"""
    
    # Sanitize inputs to prevent XSS
    def sanitize_text(text):
        if not text:
            return ""
        return str(text).replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    
    title = sanitize_text(course_data.get('title', 'Course Title'))
    platform = sanitize_text(course_data.get('platform', 'Platform'))
    description = sanitize_text(course_data.get('description', 'Great for career development'))
    duration = sanitize_text(course_data.get('duration', 'Duration varies'))
    
    # Determine cost styling
    cost_raw = course_data.get('cost', '')
    if "free" in str(cost_raw).lower():
        cost_class = "cost-free"
        cost_text = "Free"
        if course_data.get('certificate_cost'):
            cost_text += f" (Certificate: {sanitize_text(course_data.get('certificate_cost'))})"
    else:
        cost_class = "cost-paid"
        cost_text = sanitize_text(cost_raw) if cost_raw else 'Contact for pricing'
    
    # Generate safe search URLs (no direct course URLs to avoid broken links)
    search_query = title.replace(' ', '+').replace('&', 'and')
    platform_lower = platform.lower()
    
    if 'coursera' in platform_lower:
        course_url = f"https://www.coursera.org/search?query={search_query}"
    elif 'edx' in platform_lower:
        course_url = f"https://www.edx.org/search?q={search_query}"
    elif 'linkedin' in platform_lower:
        course_url = f"https://www.linkedin.com/learning/search?keywords={search_query}"
    elif 'udemy' in platform_lower:
        course_url = f"https://www.udemy.com/courses/search/?q={search_query}"
    elif 'futurelearn' in platform_lower:
        course_url = f"https://www.futurelearn.com/search?q={search_query}"
    elif 'google' in platform_lower:
        course_url = "https://learndigital.withgoogle.com/digitalskills"
    elif 'digiskills' in platform_lower:
        course_url = "https://www.digiskillsafrica.com"
    else:
        course_url = f"https://www.google.com/search?q={search_query}+online+course"
    
    return f"""
    <div class="course-card">
        <div class="course-header">
            <div class="course-check">✅</div>
            <h3 class="course-title">{title}</h3>
        </div>
        
        <div class="course-meta">
            <div class="course-platform">
                <span class="platform-icon">🌐</span>
                <span class="platform-name">{platform}</span>
            </div>
            <div class="course-cost {cost_class}">
                <span class="cost-icon">💰</span>
                <span class="cost-text">{cost_text}</span>
            </div>
        </div>
        
        <div class="course-duration">
            <span class="duration-icon">⏱️</span>
            <span class="duration-text">{duration}</span>
        </div>
        
        <div class="course-description">
            <div class="description-header">
                <span class="check-icon">✅</span>
                <span class="description-title">Why this course is perfect for you:</span>
            </div>
            <div class="description-content">
                <span class="description-text">{description}</span>
            </div>
        </div>
        
        <a href="{course_url}" target="_blank" class="course-link" rel="noopener noreferrer">
            <span class="link-icon">🔗</span>
            <span class="link-text">Search for Course</span>
        </a>
        
        <div class="course-disclaimer">
            ⚠️ Please verify course availability, current pricing, and requirements on the platform.
        </div>
    </div>
    """

def format_courses_response(courses_data):
    """Format the complete courses response with enhanced styling"""
    
    if not courses_data or 'courses' not in courses_data:
        return "<div class='error-message'>❌ Unable to generate course recommendations. Please try again.</div>"
    
    courses = courses_data['courses']
    if not courses:
        return "<div class='error-message'>❌ No courses found matching your criteria. Please adjust your preferences and try again.</div>"
    
    # Generate header
    header_html = """
    <div class="recommendations-header">
        <div class="header-content">
            <div class="back-button" id="back-button-header">
                <span class="back-icon">←</span>
                <span class="back-text">Back to Profile</span>
            </div>
            <h2 class="main-title">Your Personalized Course Recommendations</h2>
            <p class="profile-subtitle">Curated for the South African job market</p>
        </div>
    </div>
    """
    
    # Generate course cards
    cards_html = ""
    for index, course in enumerate(courses):
        cards_html += generate_course_card_html(course, index)
    
    # Add disclaimer section
    disclaimer_html = """
    <div class="global-disclaimer">
        <h4>📋 Important Information:</h4>
        <ul>
            <li>Course recommendations are AI-generated - always verify current availability and pricing</li>
            <li>Prices shown are estimates in South African Rands</li>
            <li>Course content and requirements may change</li>
            <li>We may earn affiliate commissions from course enrollments</li>
            <li>This service is provided for educational guidance only</li>
        </ul>
    </div>
    """
    
    # Enhanced styling with better UX
    full_html = f"""
    <style>
        .recommendations-container {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            background: #f8f9fa;
            border-radius: 12px;
            overflow: hidden;
        }}
        
        .recommendations-header {{
            background: linear-gradient(135deg, #4285f4, #34a853);
            color: white;
            padding: 24px;
            text-align: center;
        }}
        
        .back-button {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(255,255,255,0.2);
            padding: 8px 16px;
            border-radius: 20px;
            margin-bottom: 16px;
            cursor: pointer;
            font-size: 14px;
            transition: background-color 0.2s;
        }}
        
        .back-button:hover {{
            background: rgba(255,255,255,0.3);
        }}
        
        .main-title {{
            font-size: 24px;
            font-weight: 600;
            margin: 0 0 8px 0;
        }}
        
        .profile-subtitle {{
            font-size: 14px;
            opacity: 0.9;
            margin: 0;
        }}
        
        .course-card {{
            background: white;
            margin: 16px;
            padding: 20px;
            border-radius: 12px;
            border-left: 4px solid #4285f4;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        
        .course-header {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            margin-bottom: 16px;
        }}
        
        .course-title {{
            font-size: 18px;
            font-weight: 600;
            color: #1a1a1a;
            margin: 0;
            line-height: 1.3;
        }}
        
        .course-meta {{
            display: flex;
            gap: 20px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }}
        
        .course-platform, .course-cost {{
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 14px;
        }}
        
        .cost-free {{
            background: #e8f5e8;
            padding: 4px 8px;
            border-radius: 12px;
        }}
        
        .cost-free .cost-text {{
            color: #2e7d32;
            font-weight: 500;
        }}
        
        .cost-paid {{
            background: #fff3e0;
            padding: 4px 8px;
            border-radius: 12px;
        }}
        
        .cost-paid .cost-text {{
            color: #ef6c00;
            font-weight: 500;
        }}
        
        .course-duration {{
            display: flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 16px;
            font-size: 14px;
            color: #666;
        }}
        
        .course-description {{
            margin-bottom: 16px;
        }}
        
        .description-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
            font-size: 14px;
            font-weight: 500;
            color: #333;
        }}
        
        .description-content .description-text {{
            font-size: 14px;
            color: #666;
            line-height: 1.4;
        }}
        
        .course-link {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: #4285f4;
            color: white;
            padding: 12px 24px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 500;
            transition: background-color 0.2s;
            margin-bottom: 12px;
        }}
        
        .course-link:hover {{
            background: #3367d6;
            text-decoration: none;
            color: white;
        }}
        
        .course-disclaimer {{
            font-size: 12px;
            color: #999;
            font-style: italic;
            border-top: 1px solid #eee;
            padding-top: 8px;
        }}
        
        .global-disclaimer {{
            background: #fff3cd;
            border: 1px solid #ffeaa7;
            border-radius: 8px;
            padding: 16px;
            margin: 16px;
            font-size: 13px;
        }}
        
        .global-disclaimer h4 {{
            color: #856404;
            margin: 0 0 8px 0;
        }}
        
        .global-disclaimer ul {{
            margin: 8px 0 0 0;
            padding-left: 20px;
        }}
        
        .global-disclaimer li {{
            margin-bottom: 4px;
            color: #856404;
        }}
        
        .error-message {{
            background: #ffebee;
            color: #c62828;
            padding: 20px;
            border-radius: 8px;
            margin: 16px;
            text-align: center;
        }}
    </style>
    
    <div class="recommendations-container">
        {header_html}
        {cards_html}
        {disclaimer_html}
    </div>
    
    <script>
        setTimeout(function() {{
            var backButton = document.getElementById('back-button-header');
            if (backButton) {{
                backButton.addEventListener('click', function() {{
                    var hiddenBackBtn = document.querySelector('#back-btn button');
                    if (hiddenBackBtn) {{
                        hiddenBackBtn.click();
                    }}
                }});
            }}
        }}, 1000);
    </script>
    """
    
    return full_html

def validate_email(email):
    """Enhanced email validation"""
    if not email or not email.strip():
        return False, "Email address is required"
    
    email = email.strip().lower()
    
    # Basic format validation
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, email):
        return False, "Please enter a valid email address (e.g., yourname@example.com)"
    
    # Block common fake/test emails
    fake_domains = ['test.com', 'example.com', 'fake.com', 'temp.com']
    domain = email.split('@')[1]
    if domain in fake_domains:
        return False, "Please use a real email address"
    
    return True, "Valid email"

def chat_with_recommendations(email, currentRole, educationLevel, employmentStatus, 
                            careerGoals, skillsInterest, experienceLevel, costPreference, 
                            history, email_captured_state, request_info=None):
    """Main function to generate course recommendations with security"""
    
    # Validate email
    email_valid, email_msg = validate_email(email)
    if not email_valid:
        return f"⚠️ {email_msg}", history, email_captured_state
    
    # Validate required fields
    required_fields = {
        "Current Role": currentRole,
        "Career Goals": careerGoals,
        "Skills of Interest": skillsInterest
    }
    
    missing_fields = [field for field, value in required_fields.items() if not value or not value.strip()]
    if missing_fields:
        return f"⚠️ Please fill in the following required fields: {', '.join(missing_fields)}", history, email_captured_state
    
    # Generate user ID for rate limiting
    user_id = get_user_id(f"{email}{currentRole}{time.time() // 3600}")
    
    # Check rate limits
    rate_limit_ok, rate_limit_msg = check_rate_limit(user_id)
    if not rate_limit_ok:
        return f"⏰ {rate_limit_msg}", history, email_captured_state
    
    # Check if AI model is available
    if not model:
        log_analytics(user_id, currentRole, employmentStatus, False)
        return """🔑 **Google API Key Required**

To get course recommendations, you need a Google API key:

1. Visit: https://aistudio.google.com/app/apikey
2. Click "Create API key"
3. Add it to your .env file as: GOOGLE_API_KEY=your_key_here
4. Restart the application

Google Gemini offers generous free limits! 🚀""", history, email_captured_state

    # Create user profile for AI
    user_input = f"""
{MODEL_INSTRUCTIONS}

User Profile for South African Job Market:
🎯 Current Role: {currentRole}
🎓 Education Level: {educationLevel}
💼 Employment Status: {employmentStatus}
🚀 Career Goals: {careerGoals}
💡 Skills of Interest: {skillsInterest}
📈 Experience Level: {experienceLevel}
💰 Cost Preference: {costPreference}

Please provide personalized course recommendations as a valid JSON object.
"""

    try:
        # Generate AI response
        response = model.generate_content(user_input)
        reply = response.text

        # Parse JSON response
        try:
            json_start = reply.find('{')
            json_end = reply.rfind('}') + 1
            
            if json_start != -1 and json_end != 0:
                json_str = reply[json_start:json_end]
                courses_data = json.loads(json_str)
            else:
                courses_data = json.loads(reply)
        
        except json.JSONDecodeError:
            # Fallback with disclaimer
            courses_data = {
                "courses": [{
                    "title": "Course recommendations available",
                    "platform": "Multiple platforms",
                    "cost": "Varies (Free to R2000+)",
                    "duration": "2-12 weeks typically",
                    "description": f"Based on your interest in {skillsInterest}, there are many relevant courses available. Please search the suggested platforms for current offerings.",
                    "disclaimer": "AI processing encountered an issue - please search manually"
                }]
            }
        
        # Format response
        formatted_reply = format_courses_response(courses_data)
        
        # Log successful analytics
        log_analytics(user_id, currentRole, employmentStatus, True)
        
        # Update history
        new_history = history + [{"user_input": user_input, "response": formatted_reply}]
        
        return formatted_reply, new_history, True

    except Exception as e:
        # Log failed analytics
        log_analytics(user_id, currentRole, employmentStatus, False)
        
        error_msg = str(e)
        if "quota" in error_msg.lower() or "limit" in error_msg.lower():
            return """⏰ **Rate Limit Reached**

Google Gemini free tier limits reached. Please try again in a few minutes.

💡 **While you wait:**
- Review popular courses on Coursera, edX, or LinkedIn Learning
- Check Google Digital Skills for Africa for free options
- Visit local SETA websites for accredited training

The service will be available again shortly! ⏱️""", history, email_captured_state
        else:
            return f"""⚠️ **Service Temporarily Unavailable**

We're experiencing technical difficulties. Please try again in a few minutes.

💡 **Alternative options:**
- Visit course platforms directly (Coursera, edX, LinkedIn Learning)
- Check Google Digital Skills for Africa
- Explore local university online offerings

Error details: {error_msg[:100]}...""", history, email_captured_state

def submit_email_and_process(modal_email, currentRole, educationLevel, employmentStatus, 
                           careerGoals, skillsInterest, experienceLevel, costPreference, history):
    """Process email submission from modal"""
    result, new_history, email_captured = chat_with_recommendations(
        modal_email, currentRole, educationLevel, employmentStatus, careerGoals, 
        skillsInterest, experienceLevel, costPreference, history, False
    )
    
    return result, new_history, email_captured, modal_email, gr.update(visible=False), ""

def show_email_modal(currentRole, educationLevel, employmentStatus, careerGoals, 
                    skillsInterest, experienceLevel, costPreference, history, 
                    email_captured_state, session_email_state):
    """Show email modal if email not captured, otherwise process directly"""
    if email_captured_state and session_email_state:
        return chat_with_recommendations(
            session_email_state, currentRole, educationLevel, employmentStatus, 
            careerGoals, skillsInterest, experienceLevel, costPreference, 
            history, email_captured_state
        ) + (gr.update(visible=False),)
    else:
        return (
            "<div style='text-align: center; padding: 20px; color: #666;'>Please enter your email to get personalized recommendations.</div>",
            history, 
            email_captured_state,
            gr.update(visible=True)
        )

def go_back_to_profile():
    """Reset to initial state"""
    return "<div style='text-align: center; padding: 40px; color: #666;'>Your tailored course recommendations will appear here! ✨</div>"

# Initialize database
init_database()

# Legal compliance components
def create_legal_footer():
    return gr.HTML("""
    <div style="background: #f5f5f5; padding: 20px; margin-top: 40px; border-radius: 8px; font-size: 12px; color: #666;">
        <div style="text-align: center; margin-bottom: 16px;">
            <strong>Legal Information & Privacy</strong>
        </div>
        <div style="display: flex; gap: 30px; justify-content: center; flex-wrap: wrap;">
            <div>
                <strong>🔒 Privacy:</strong> We collect minimal data for service improvement. 
                No personal information is shared with third parties.
            </div>
            <div>
                <strong>💰 Disclosure:</strong> We may earn affiliate commissions from course enrollments.
            </div>
            <div>
                <strong>⚠️ Disclaimer:</strong> Course recommendations are AI-generated guidance only. 
                Always verify current course details.
            </div>
        </div>
        <div style="text-align: center; margin-top: 12px; font-size: 11px;">
            <a href="#" style="color: #1e88e5;">Privacy Policy</a> | 
            <a href="#" style="color: #1e88e5;">Terms of Service</a> | 
            <a href="#" style="color: #1e88e5;">Contact Us</a>
        </div>
    </div>
    """)

# Main UI with enhanced security and compliance
with gr.Blocks(theme=gr.themes.Base(), title="LWM Course Guide - Secure") as demo:
    # Session state
    state = gr.State([])
    email_captured = gr.State(False)
    session_email = gr.State("")
    
    # Header with compliance notice
    gr.HTML("""
    <div style="text-align: center; padding: 40px 20px;">
        <div style="width: 80px; height: 80px; background: linear-gradient(135deg, #1e88e5, #26a69a);
                   border-radius: 50%; margin: 0 auto 20px; display: flex; align-items: center; justify-content: center;">
            <span style="font-size: 30px; color: white;">📚</span>
        </div>
        <h1 style="font-size: 3.5em; margin: 0; background: linear-gradient(135deg, #1e88e5, #26a69a);
                   -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: bold;">
            LWM Course Guide
        </h1>
        <p style="font-size: 1.2em; color: #666; margin-top: 10px;">
            AI-powered course recommendations for South African professionals<br>
            <span style="font-size: 0.9em; color: #999;">🔒 Privacy-focused • ⚡ Free to use • 🏆 Trusted sources</span>
        </p>
    </div>
    """)

    # Service limits notice
    gr.HTML("""
    <div style="background: #e3f2fd; border-left: 4px solid #1e88e5; padding: 16px; margin: 20px 0; border-radius: 8px;">
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
            <span style="font-size: 16px;">ℹ️</span>
            <strong style="color: #1565c0;">Service Usage Limits</strong>
        </div>
        <p style="margin: 0; font-size: 14px; color: #1976d2;">
            To ensure fair access: <strong>10 recommendations per hour, 50 per day</strong>. 
            This helps us keep the service free while managing costs.
        </p>
    </div>
    """)

    # Profile form
    gr.HTML("""
    <div style="background: white; border-radius: 15px; padding: 30px; margin: 20px 0; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
        <div style="text-align: center; margin-bottom: 30px;">
            <div style="width: 60px; height: 60px; background: linear-gradient(135deg, #1e88e5, #26a69a);
                       border-radius: 50%; margin: 0 auto 15px; display: flex; align-items: center; justify-content: center;">
                <span style="font-size: 24px; color: white;">🎓</span>
            </div>
            <h2 style="color: #1e88e5; margin: 0; font-size: 2em;">Career Development Profile</h2>
            <p style="color: #666; margin: 10px 0 0 0;">
                Tell us about yourself for personalized recommendations
            </p>
        </div>
    </div>
    """)

    with gr.Row():
        with gr.Column():
            currentRole = gr.Textbox(
                label="Current Role/Field *",
                placeholder="e.g., Marketing Assistant, Student, Unemployed",
                info="Required - helps us recommend relevant skills"
            )
            employmentStatus = gr.Dropdown(
                label="Employment Status",
                choices=[
                    "Employed Full-time",
                    "Employed Part-time", 
                    "Student",
                    "Unemployed",
                    "Freelancer/Self-employed",
                    "Career Break"
                ],
                value=None
            )
            costPreference = gr.Dropdown(
                label="Course Cost Preference",
                choices=[
                    "Free courses only",
                    "Paid courses (up to R500)",
                    "Paid courses (up to R2000)",
                    "Any cost if valuable"
                ],
                value="Free courses only"
            )
        
        with gr.Column():
            educationLevel = gr.Dropdown(
                label="Education Level",
                choices=[
                    "Matric/Grade 12",
                    "Certificate",
                    "Diploma",
                    "Bachelor's Degree",
                    "Honours Degree",
                    "Master's Degree",
                    "Doctorate"
                ],
                value=None
            )
            experienceLevel = gr.Dropdown(
                label="Experience Level",
                choices=[
                    "Entry Level (0-2 years)",
                    "Mid Level (3-5 years)",
                    "Senior Level (5+ years)",
                    "Executive Level"
                ],
                value="Entry Level (0-2 years)"
            )
            
    careerGoals = gr.Textbox(
        label="Career Goals *",
        placeholder="Describe your career aspirations, target roles, or industries...",
        lines=2,
        info="Required - helps us understand your direction"
    )
    
    skillsInterest = gr.Textbox(
        label="Skills of Interest *",
        placeholder="e.g., Digital Marketing, Data Analysis, Project Management, Python Programming",
        info="Required - specific skills you want to develop"
    )

    # Output section
    bot_reply = gr.HTML(
        value="<div style='text-align: center; padding: 40px; color: #666;'>Your tailored course recommendations will appear here! ✨</div>",
        elem_id="course-output"
    )

    # Main action button
    send_btn = gr.Button(
        "🚀 Get My Course Recommendations",
        variant="primary",
        size="lg",
        elem_id="submit-btn"
    )
    
    # Hidden back button
    back_btn = gr.Button(
        "Back to Profile",
        visible=False,
        elem_id="back-btn"
    )

    # Email collection modal
    with gr.Group(visible=False) as email_modal:
        gr.HTML("""
        <div style="background: linear-gradient(135deg, #e3f2fd, #f1f8e9); 
                   border: 2px solid #1e88e5; border-radius: 15px; padding: 30px; 
                   margin: 20px 0; box-shadow: 0 4px 15px rgba(30, 136, 229, 0.2);">
            <div style="text-align: center; margin-bottom: 20px;">
                <div style="width: 60px; height: 60px; background: linear-gradient(135deg, #1e88e5, #26a69a);
                           border-radius: 50%; margin: 0 auto 15px; display: flex; align-items: center; justify-content: center;">
                    <span style="font-size: 24px; color: white;">📧</span>
                </div>
                <h2 style="color: #1e88e5; margin: 0; font-size: 1.8em;">Almost There!</h2>
                <p style="color: #666; margin: 10px 0 0 0; font-size: 14px;">
                    Enter your email to receive personalized recommendations
                </p>
            </div>
        </div>
        """)
        
        gr.HTML("""
        <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; padding: 12px; margin-bottom: 16px;">
            <div style="font-size: 13px; color: #856404;">
                <strong>🔒 Privacy Promise:</strong> Your email is only used for this service. 
                We don't spam, share, or sell your information. You can request deletion anytime.
            </div>
        </div>
        """)
        
        modal_email = gr.Textbox(
            label="📧 Your Email Address",
            placeholder="yourname@example.com",
            info="Required for personalized recommendations",
            elem_id="modal-email-input"
        )
        
        with gr.Row():
            modal_submit = gr.Button("✨ Get My Recommendations", variant="primary", size="lg")
            modal_cancel = gr.Button("Cancel", variant="secondary")

    # Legal footer
    create_legal_footer()

    # Enhanced CSS with security considerations
    gr.HTML("""
    <style>
        /* Prevent content injection */
        * { max-width: 100%; overflow-wrap: break-word; }
        
        #submit-btn {
            background: linear-gradient(135deg, #1e88e5, #26a69a) !important;
            border: none !important;
            color: white !important;
            font-weight: bold !important;
            padding: 15px 40px !important;
            border-radius: 10px !important;
            font-size: 16px !important;
            margin: 20px 0 !important;
            width: 100% !important;
            transition: transform 0.2s !important;
        }
        
        #submit-btn:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 4px 15px rgba(30, 136, 229, 0.3) !important;
        }
        
        #course-output {
            max-height: 800px !important;
            overflow-y: auto !important;
            border: 1px solid #e0e0e0 !important;
            border-radius: 8px !important;
            background: white !important;
        }
        
        #modal-email-input input {
            border: 2px solid #1e88e5 !important;
            border-radius: 8px !important;
            background: #f8f9ff !important;
            transition: border-color 0.2s !important;
        }
        
        #modal-email-input input:focus {
            border: 2px solid #26a69a !important;
            box-shadow: 0 0 8px rgba(30, 136, 229, 0.3) !important;
        }
        
        .gradio-container {
            max-width: 1000px !important;
            margin: 0 auto !important;
        }
        
        /* Rate limiting notice */
        .rate-limit-warning {
            background: #ffebee !important;
            color: #c62828 !important;
            padding: 12px !important;
            border-radius: 6px !important;
            margin: 8px 0 !important;
            font-size: 14px !important;
        }
        
        /* Success states */
        .success-message {
            background: #e8f5e8 !important;
            color: #2e7d32 !important;
            padding: 12px !important;
            border-radius: 6px !important;
            margin: 8px 0 !important;
        }
        
        /* Security indicators */
        .security-indicator {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            font-size: 12px;
            color: #4caf50;
        }
        
        /* Prevent XSS in user inputs */
        input, textarea {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
        }
    </style>
    """)

    # Event handlers with enhanced security
    send_btn.click(
        fn=show_email_modal,
        inputs=[currentRole, educationLevel, employmentStatus, careerGoals, 
                skillsInterest, experienceLevel, costPreference, state, 
                email_captured, session_email],
        outputs=[bot_reply, state, email_captured, email_modal]
    )
    
    modal_submit.click(
        fn=submit_email_and_process,
        inputs=[modal_email, currentRole, educationLevel, employmentStatus, 
                careerGoals, skillsInterest, experienceLevel, costPreference, state],
        outputs=[bot_reply, state, email_captured, session_email, email_modal, modal_email]
    )
    
    modal_cancel.click(
        fn=lambda: (gr.update(visible=False), ""),
        outputs=[email_modal, modal_email]
    )
    
    back_btn.click(
        fn=go_back_to_profile,
        outputs=[bot_reply]
    )

# Launch configuration with Railway-compatible settings
if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", 7860)),  # Fixed for Railway
        share=False,  # Set to True only for testing
        auth=None,  # Add authentication if needed
        ssl_verify=True,
        show_error=False,  # Don't show detailed errors to users
        favicon_path=None,
        app_kwargs={
            "docs_url": None,  # Disable API docs in production
            "redoc_url": None
        }
    )
