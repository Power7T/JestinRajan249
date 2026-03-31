# HostAI

**Fast, intelligent hosting operations platform for short-term rental properties.**

HostAI is a complete SaaS solution that automates guest communication, manages properties, handles billing, and provides AI-powered voice calling—all designed for hosting businesses and property managers.

![Status](https://img.shields.io/badge/status-production-green) ![Tech](https://img.shields.io/badge/stack-FastAPI%20%7C%20React%20%7C%20PostgreSQL-blue)

---

## 🎯 Core Features

### Guest Communication
- **Multi-channel messaging**: Email, WhatsApp, SMS (Twilio/Meta)
- **AI-powered drafts**: Intelligent reply suggestions with approval workflow
- **Conversation history**: Full context and timeline per guest
- **Smart routing**: Auto-categorize and route issues (support, maintenance, billing)

### Voice AI Calling
- **Inbound voice calls**: AI answers with guest context and property knowledge
- **Guest identification**: Phone number → name → confirmation code fallbacks
- **Multi-language**: Auto-detects language, responds accordingly
- **Recording & transcripts**: Full call records with AI summaries
- **Knowledge gap tracking**: Auto-escalate unanswered questions to tickets

### Property Management
- **Reservation management**: CSV import, PMS sync (Airbnb, Vrbo, etc.), manual entry
- **iCal sync**: Real-time calendar integration
- **Guest database**: Multi-property guest tracking with history
- **Vendor routing**: Auto-route maintenance/cleaning requests
- **Activity timeline**: Guest interactions, reservations, and workflows

### Billing & Monetization
- **Multi-tier pricing**: Starter ($20), Growth ($20), Pro ($20) based on units
- **Voice add-ons**: Light ($39), Standard ($79), Professional ($129), Unlimited ($199)
- **Usage tracking**: Real-time API cost monitoring per tenant
- **Admin pricing panel**: Adjust voice pricing dynamically without code
- **Margin analysis**: Track profitability by pricing tier

### Admin Dashboard
- **Multi-tenant management**: Complete SaaS infrastructure
- **Real-time metrics**: Revenue, churn, customer acquisition, voice analytics
- **Pricing administration**: Update voice AI pricing in real-time
- **Audit logging**: Complete activity trail for compliance

## 🎤 Voice AI System

Complete AI-powered phone system for your properties:

```
Guest calls → Twilio answer → AI greeting (personalized with guest context)
↓
Guest speaks question → Deepgram (speech-to-text)
↓
OpenAI/Llama analyzes with property context → generates response
↓
ElevenLabs speaks response → guest hears answer
↓
Full transcript + recording saved → escalations to you via SMS
```

**Key capabilities:**
- Guest identification via phone, name, or confirmation code
- Property context (amenities, rules, check-in info) in responses
- Multi-language auto-detection and response
- Knowledge gap tracking (unanswered questions → tickets)
- Real-time cost monitoring and usage analytics

See **[Voice AI Documentation](docs/VOICE_AI.md)** for complete details.

---

## 💰 Pricing Strategy

**Unit-based plans** (per property):
- **Starter**: $20/month (1-5 properties)
- **Growth**: $20/month (6-10 properties)
- **Pro**: $20/month (11-50 properties)

**Voice AI add-ons** (monthly subscription):
| Tier | Price | Minutes | Overage |
|------|-------|---------|---------|
| Light | $39 | 100 | $0.049/min |
| Standard | $79 | 300 | $0.049/min |
| Professional | $129 | 750 | $0.049/min |
| Unlimited | $199 | Unlimited | Free |

See **[Pricing Documentation](docs/PRICING.md)** for strategy and margin analysis.

---

## 🏗️ Architecture

```
Frontend (React) → FastAPI Backend → PostgreSQL
                 ↘ WebSocket      ↘ Redis (cache/queue)
                 ↘ Webhooks       ↘ R2 (storage)
                                  ↘ IMAP (email)

Background Worker → Email polling, Task scheduling, PMS sync
```

**Tech Stack:**
- **Frontend**: React + Tailwind CSS (Obsidian dark theme)
- **Backend**: FastAPI + SQLAlchemy
- **Database**: PostgreSQL + Redis
- **Voice**: Twilio + Deepgram + OpenAI/Llama + ElevenLabs
- **Storage**: Cloudflare R2
- **Billing**: Stripe
- **Deployment**: Docker + Railway + Nginx

See **[Architecture Documentation](docs/ARCHITECTURE.md)** for detailed system design.

## 📚 Documentation

Complete documentation in the `/docs` directory:

- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System design, data flow, tech stack
- **[FEATURES.md](docs/FEATURES.md)** - Complete feature list and capabilities
- **[VOICE_AI.md](docs/VOICE_AI.md)** - Voice calling system, pricing, usage
- **[PRICING.md](docs/PRICING.md)** - Billing strategy, pricing structure, margin analysis
- **[SETUP.md](docs/SETUP.md)** - Local development setup and configuration
- **[DEPLOYMENT.md](docs/DEPLOYMENT.md)** - Production deployment guide
- **[API.md](docs/API.md)** - REST API endpoints and usage
- **[ADMIN.md](docs/ADMIN.md)** - Admin panel features and management
- **[SECURITY.md](docs/SECURITY.md)** - Security practices and compliance
- **[DEVELOPMENT.md](docs/DEVELOPMENT.md)** - Code structure and development guide

---

## 🚀 Quick Start

### Local Development

**Requirements:**
- Python 3.12+, PostgreSQL 15+, Redis 7+, Docker & Docker Compose

**Setup:**
```bash
git clone https://github.com/Power7T/JestinRajan249.git
cd BNB

# Create environment file from template
cp .env.example .env
# Edit .env with your configuration

# Start all services
docker compose -f docker-compose.dev.yml up -d --build

# Run database migrations
alembic upgrade head

# Access the app
```

**Open in browser:**
- App: http://localhost:8000
- Admin: http://localhost:8000/admin
- Default credentials: `admin@hostai.local` / (set in .env)

### Production Deployment

```bash
cp .env.example .env
# Add production secrets to .env (see DEPLOYMENT.md)
git push origin main  # Deploy to Railway
```

See **[Deployment Documentation](docs/DEPLOYMENT.md)** for full setup.

---

## 🔑 Environment Configuration

All configuration via environment variables. **Do NOT commit secrets to the repository.**

Key variables (see `.env.example` for complete list):
```
DATABASE_URL              # PostgreSQL connection string
REDIS_URL                 # Redis connection string
SECRET_KEY                # Session encryption key
STRIPE_API_KEY            # Stripe billing API (get from Stripe dashboard)
TWILIO_ACCOUNT_SID        # Twilio voice API (get from Twilio console)
OPENROUTER_API_KEY        # LLM API (get from OpenRouter)
```

**Getting API keys:**
- Stripe: https://dashboard.stripe.com
- Twilio: https://console.twilio.com
- OpenRouter: https://openrouter.ai

Never commit these keys. Use `.env.example` as a template.

---

## 📈 Key Features in Detail

### Email Integration
- **Forwarding mode**: Webhook-based (Mailgun, Postmark, custom)
- **IMAP mode**: Direct mailbox polling with worker
- Full email thread tracking and context

### Reservation Management
- **CSV upload**: Simple Airbnb/Vrbo export
- **PMS sync**: Airbnb, Vrbo, Booking.com integrations
- **Manual entry**: Add guests and reservations directly
- **iCal sync**: Real-time calendar updates

### Workflow Automation
- **Guest messaging**: Email, WhatsApp, SMS
- **Draft generation**: AI-powered replies with human review
- **Issue routing**: Auto-categorize and assign to teams
- **Vendor management**: Schedule and track maintenance

### Analytics & Reporting
- **Real-time dashboards**: Revenue, churn, customer metrics
- **Voice analytics**: Call volume, sentiment, cost analysis
- **Property metrics**: Utilization, capacity, pricing performance
- **Export**: CSV and PDF reports

---

## 🔐 Security & Compliance

- **CSRF protection**: Token-based validation on all state changes
- **Webhook signature validation**: Twilio, Meta, Mailgun signatures verified
- **Rate limiting**: Per-tenant and per-IP limits
- **Audit logging**: Complete activity trail for compliance
- **Data encryption**: Sensitive fields encrypted at rest
- **GDPR/CCPA ready**: Recording consent, data retention policies

See **[Security Documentation](docs/SECURITY.md)** for details.

---

## 🛠️ Development

**Common commands:**
```bash
# Run migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Run tests
pytest web/tests/ -v

# Code formatting
black web/ worker/

# Linting
pylint web/
```

See **[Development Guide](docs/DEVELOPMENT.md)** for code structure and conventions.

---

## 📊 Project Status

✅ **Production-ready:**
- Multi-tenant SaaS infrastructure
- Voice AI calling system
- Admin pricing panel
- Guest data integration
- Real-time analytics

🚀 **Active development:**
- Advanced voice analytics
- Call routing & escalation
- Team collaboration features

See **[Roadmap](docs/ROADMAP.md)** for 2026 plans.

---

## 🤝 Contributing

Bug reports and pull requests welcome! See **[Contributing Guidelines](docs/CONTRIBUTING.md)**.

---

## 📄 License

Proprietary. All rights reserved.

---

## 📞 Support

- **Documentation**: See `/docs` directory
- **Issues**: GitHub Issues
- **Questions**: Review documentation first

---

**Built with ❤️ for hosting businesses worldwide.**
