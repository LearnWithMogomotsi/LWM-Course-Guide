#!/usr/bin/env python3
"""
Database Monitor Script for Railway PostgreSQL
Monitor users, analytics, and system health
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta
import json
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

class DatabaseMonitor:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        
        if not self.database_url:
            # Fallback for Railway auto-provided variables
            db_host = os.getenv("PGHOST")
            db_port = os.getenv("PGPORT", "5432")
            db_name = os.getenv("PGDATABASE")
            db_user = os.getenv("PGUSER")
            db_password = os.getenv("PGPASSWORD")
            
            if all([db_host, db_name, db_user, db_password]):
                self.database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        if self.database_url:
            parsed = urlparse(self.database_url)
            self.db_config = {
                'host': parsed.hostname,
                'port': parsed.port or 5432,
                'database': parsed.path[1:],
                'user': parsed.username,
                'password': parsed.password,
                'sslmode': 'require'
            }
        else:
            raise ValueError("PostgreSQL connection details not found")

    def get_connection(self):
        """Get database connection"""
        try:
            return psycopg2.connect(**self.db_config)
        except Exception as e:
            print(f"❌ Database connection failed: {e}")
            return None

    def get_daily_stats(self, days=7):
        """Get daily usage statistics"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                DATE(timestamp) as date,
                COUNT(*) as total_requests,
                COUNT(*) FILTER (WHERE request_success = true) as successful_requests,
                COUNT(*) FILTER (WHERE request_success = false) as failed_requests,
                COUNT(DISTINCT user_id_hash) as unique_users,
                ROUND(AVG(processing_time_ms), 2) as avg_processing_time_ms
            FROM analytics 
            WHERE timestamp >= CURRENT_DATE - INTERVAL '%s days'
            GROUP BY DATE(timestamp)
            ORDER BY date DESC;
            """
            
            cursor.execute(query, (days,))
            results = cursor.fetchall()
            
            print(f"\n📊 DAILY STATISTICS (Last {days} days)")
            print("=" * 80)
            print(f"{'Date':<12} {'Total':<8} {'Success':<8} {'Failed':<8} {'Users':<8} {'Avg Time(ms)':<12}")
            print("-" * 80)
            
            total_requests = 0
            total_users = set()
            
            for row in results:
                total_requests += row['total_requests']
                print(f"{row['date']:<12} {row['total_requests']:<8} {row['successful_requests']:<8} "
                      f"{row['failed_requests']:<8} {row['unique_users']:<8} {row['avg_processing_time_ms'] or 'N/A':<12}")
            
            print("-" * 80)
            print(f"Total Requests: {total_requests}")
            
            return results
            
        except Exception as e:
            print(f"❌ Error getting daily stats: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_user_profiles(self, limit=20):
        """Get recent user profiles"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                id,
                user_id_hash,
                current_role,
                education_level,
                employment_status,
                career_goals,
                skills_interest,
                experience_level,
                cost_preference,
                created_at,
                updated_at
            FROM user_profiles 
            ORDER BY created_at DESC 
            LIMIT %s;
            """
            
            cursor.execute(query, (limit,))
            results = cursor.fetchall()
            
            print(f"\n👥 RECENT USER PROFILES (Last {len(results)})")
            print("=" * 120)
            
            for row in results:
                print(f"\n🆔 User Hash: {row['user_id_hash']}")
                print(f"   Role: {row['current_role']}")
                print(f"   Education: {row['education_level']}")
                print(f"   Status: {row['employment_status']}")
                print(f"   Goals: {row['career_goals'][:80]}{'...' if len(row['career_goals'] or '') > 80 else ''}")
                print(f"   Skills: {row['skills_interest'][:80]}{'...' if len(row['skills_interest'] or '') > 80 else ''}")
                print(f"   Experience: {row['experience_level']}")
                print(f"   Cost Pref: {row['cost_preference']}")
                print(f"   Created: {row['created_at']}")
                print("-" * 120)
            
            return results
            
        except Exception as e:
            print(f"❌ Error getting user profiles: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_popular_career_fields(self, limit=10):
        """Get most popular career fields"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                career_field,
                COUNT(*) as request_count,
                COUNT(DISTINCT user_id_hash) as unique_users,
                COUNT(*) FILTER (WHERE request_success = true) as successful_requests,
                ROUND(
                    (COUNT(*) FILTER (WHERE request_success = true)::float / COUNT(*)) * 100, 
                    1
                ) as success_rate_percent
            FROM analytics 
            WHERE career_field IS NOT NULL 
            AND timestamp >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY career_field 
            ORDER BY request_count DESC 
            LIMIT %s;
            """
            
            cursor.execute(query, (limit,))
            results = cursor.fetchall()
            
            print(f"\n🔥 TOP CAREER FIELDS (Last 30 days)")
            print("=" * 90)
            print(f"{'Career Field':<30} {'Requests':<10} {'Users':<8} {'Success':<10} {'Rate %':<8}")
            print("-" * 90)
            
            for row in results:
                print(f"{row['career_field'][:29]:<30} {row['request_count']:<10} "
                      f"{row['unique_users']:<8} {row['successful_requests']:<10} "
                      f"{row['success_rate_percent']:<8}")
            
            return results
            
        except Exception as e:
            print(f"❌ Error getting career fields: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_recent_recommendations(self, limit=10):
        """Get recent course recommendations"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                user_id_hash,
                session_id,
                courses_count,
                success,
                created_at,
                recommendation_data
            FROM recommendations 
            ORDER BY created_at DESC 
            LIMIT %s;
            """
            
            cursor.execute(query, (limit,))
            results = cursor.fetchall()
            
            print(f"\n📝 RECENT RECOMMENDATIONS (Last {len(results)})")
            print("=" * 100)
            
            for row in results:
                print(f"\n🆔 User: {row['user_id_hash']}")
                print(f"   Session: {row['session_id']}")
                print(f"   Courses: {row['courses_count']} | Success: {'✅' if row['success'] else '❌'}")
                print(f"   Created: {row['created_at']}")
                
                # Show first course recommendation
                if row['recommendation_data'] and 'courses' in row['recommendation_data']:
                    courses = row['recommendation_data']['courses']
                    if courses:
                        first_course = courses[0]
                        print(f"   First Course: {first_course.get('title', 'N/A')} ({first_course.get('platform', 'N/A')})")
                
                print("-" * 100)
            
            return results
            
        except Exception as e:
            print(f"❌ Error getting recommendations: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_rate_limit_stats(self):
        """Get rate limiting statistics"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
            SELECT 
                COUNT(*) as total_users,
                COUNT(*) FILTER (WHERE hourly_count >= 8) as near_hourly_limit,
                COUNT(*) FILTER (WHERE daily_count >= 40) as near_daily_limit,
                AVG(hourly_count) as avg_hourly_usage,
                AVG(daily_count) as avg_daily_usage,
                MAX(daily_count) as max_daily_usage
            FROM rate_limits;
            """
            
            cursor.execute(query)
            stats = cursor.fetchone()
            
            print(f"\n⏰ RATE LIMITING STATISTICS")
            print("=" * 60)
            print(f"Total Users: {stats['total_users']}")
            print(f"Near Hourly Limit (8+): {stats['near_hourly_limit']}")
            print(f"Near Daily Limit (40+): {stats['near_daily_limit']}")
            print(f"Avg Hourly Usage: {stats['avg_hourly_usage']:.1f}")
            print(f"Avg Daily Usage: {stats['avg_daily_usage']:.1f}")
            print(f"Max Daily Usage: {stats['max_daily_usage']}")
            
            # Get top users by usage
            cursor.execute("""
                SELECT user_id, daily_count, last_day_reset 
                FROM rate_limits 
                WHERE daily_count > 5 
                ORDER BY daily_count DESC 
                LIMIT 5;
            """)
            
            top_users = cursor.fetchall()
            if top_users:
                print(f"\n🔥 Top Users by Daily Usage:")
                for user in top_users:
                    print(f"   {user['user_id']}: {user['daily_count']} requests (last reset: {user['last_day_reset']})")
            
            return stats
            
        except Exception as e:
            print(f"❌ Error getting rate limit stats: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def get_error_analysis(self):
        """Analyze errors and failures"""
        conn = self.get_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get error messages
            query = """
            SELECT 
                error_message,
                COUNT(*) as error_count,
                MAX(timestamp) as last_occurrence
            FROM analytics 
            WHERE request_success = false 
            AND error_message IS NOT NULL
            AND timestamp >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY error_message 
            ORDER BY error_count DESC 
            LIMIT 10;
            """
            
            cursor.execute(query)
            errors = cursor.fetchall()
            
            print(f"\n🚨 ERROR ANALYSIS (Last 7 days)")
            print("=" * 100)
            
            if errors:
                for error in errors:
                    print(f"Count: {error['error_count']:<5} | Last: {error['last_occurrence']}")
                    print(f"Error: {error['error_message'][:80]}{'...' if len(error['error_message']) > 80 else ''}")
                    print("-" * 100)
            else:
                print("✅ No errors found in the last 7 days!")
            
            return errors
            
        except Exception as e:
            print(f"❌ Error analyzing errors: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def run_full_report(self):
        """Run complete monitoring report"""
        print("🚀 LWM Course Guide - Database Monitoring Report")
        print("=" * 80)
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Get all statistics
        self.get_daily_stats(7)
        self.get_popular_career_fields(10)
        self.get_rate_limit_stats()
        self.get_error_analysis()
        self.get_recent_recommendations(5)
        self.get_user_profiles(10)
        
        print("\n" + "=" * 80)
        print("✅ Monitoring report completed!")

def main():
    """Main function with command line options"""
    import sys
    
    monitor = DatabaseMonitor()
    
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        
        if command == "daily":
            days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
            monitor.get_daily_stats(days)
        elif command == "users":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
            monitor.get_user_profiles(limit)
        elif command == "careers":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            monitor.get_popular_career_fields(limit)
        elif command == "recommendations":
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            monitor.get_recent_recommendations(limit)
        elif command == "rates":
            monitor.get_rate_limit_stats()
        elif command == "errors":
            monitor.get_error_analysis()
        else:
            print("Available commands:")
            print("  daily [days]         - Daily statistics")
            print("  users [limit]        - Recent user profiles")
            print("  careers [limit]      - Popular career fields")
            print("  recommendations [limit] - Recent recommendations")
            print("  rates                - Rate limiting stats")
            print("  errors               - Error analysis")
            print("  (no command)         - Full report")
    else:
        monitor.run_full_report()

if __name__ == "__main__":
    main()
