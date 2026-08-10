#!/usr/bin/env bash
# =============================================================================
# SilentWaze — AWS RDS Setup Script
# Run this ONCE to provision the database on AWS.
# Requires: AWS CLI configured (aws configure) with sufficient IAM permissions.
# =============================================================================

set -euo pipefail

# ── Config (edit these) ───────────────────────────────────────────────────────
DB_INSTANCE_ID="silentwaze-db"
DB_NAME="silentwaze"
DB_USER="silentwaze_admin"
DB_PASSWORD="ChangeMe_S3cure!"       # ← change before running
DB_CLASS="db.t3.micro"              # free-tier eligible
ENGINE_VERSION="15.4"
REGION="ap-southeast-2"             # Sydney — closest to Melbourne
SECURITY_GROUP_ID=""                # ← fill in your existing SG or create one below
SUBNET_GROUP=""                     # ← fill in your DB subnet group name

# ── Step 1: Create RDS PostgreSQL instance ────────────────────────────────────
echo "Creating RDS instance: $DB_INSTANCE_ID ..."

aws rds create-db-instance \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --db-instance-class       "$DB_CLASS" \
    --engine                  postgres \
    --engine-version          "$ENGINE_VERSION" \
    --master-username         "$DB_USER" \
    --master-user-password    "$DB_PASSWORD" \
    --db-name                 "$DB_NAME" \
    --allocated-storage       20 \
    --storage-type            gp2 \
    --no-multi-az \
    --publicly-accessible \
    --vpc-security-group-ids  "$SECURITY_GROUP_ID" \
    --db-subnet-group-name    "$SUBNET_GROUP" \
    --region                  "$REGION" \
    --backup-retention-period 7 \
    --tags Key=Project,Value=SilentWaze Key=Team,Value=Silento

echo "Waiting for RDS instance to become available (this takes ~5 minutes)..."
aws rds wait db-instance-available \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$REGION"

# ── Step 2: Get the endpoint ──────────────────────────────────────────────────
ENDPOINT=$(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_ID" \
    --region "$REGION" \
    --query "DBInstances[0].Endpoint.Address" \
    --output text)

echo ""
echo "✅  RDS instance ready!"
echo "    Endpoint: $ENDPOINT"
echo "    Port:     5432"
echo ""
echo "DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@$ENDPOINT:5432/$DB_NAME"
echo ""
echo "Copy the DATABASE_URL above into your .env file."

# ── Step 3: Apply schema ──────────────────────────────────────────────────────
echo "Applying schema..."
PGPASSWORD="$DB_PASSWORD" psql \
    -h "$ENDPOINT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -f "$(dirname "$0")/schema.sql"

echo "✅  Schema applied."
echo ""
echo "Next step: run the data loader:"
echo "    python database/load_data.py"
