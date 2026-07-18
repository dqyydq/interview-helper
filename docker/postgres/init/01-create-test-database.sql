SELECT 'CREATE DATABASE interview_helper_test OWNER interview_helper'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'interview_helper_test')\gexec
