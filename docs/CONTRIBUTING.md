# Contributing Guidelines

Thank you for interest in contributing to HostAI! This guide covers how to contribute code, report bugs, and improve documentation.

## Code of Conduct

Be respectful, inclusive, and professional. We don't tolerate discrimination, harassment, or abuse.

## Getting Started

### 1. Fork and Clone

```bash
# Fork on GitHub (click "Fork" button)

# Clone your fork
git clone https://github.com/your-username/hostai.git
cd hostai

# Add upstream remote
git remote add upstream https://github.com/power7t/hostai.git
```

### 2. Create Feature Branch

```bash
# Update main
git fetch upstream
git checkout main
git rebase upstream/main

# Create feature branch
git checkout -b feature/your-feature-name
```

### 3. Set Up Development Environment

```bash
# See SETUP.md for detailed instructions
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml exec web alembic upgrade head
```

## Making Changes

### Code Quality

All code must:
- ✅ Follow PEP 8 style guide
- ✅ Pass linting (pylint, mypy)
- ✅ Have type hints
- ✅ Include docstrings for public APIs
- ✅ Pass existing tests
- ✅ Include new tests for new functionality

### Before Submitting

```bash
# Format code
black web/ worker/ frontend/

# Run linter
pylint web/ --disable=C0111

# Type check
mypy web/ --ignore-missing-imports

# Run tests
pytest web/tests/ -v

# Check for vulnerabilities
pip-audit
```

### Commit Messages

Use clear, descriptive commit messages:

```bash
# ✅ Good
git commit -m "feat: Add guest phone number validation in voice system"
git commit -m "fix: Prevent voice call context from including inactive guests"
git commit -m "docs: Document multi-tenant isolation in API"

# ❌ Bad
git commit -m "fixed stuff"
git commit -m "updates"
git commit -m "WIP"
```

**Format:**
```
<type>: <subject>

<body (optional)>

<footer (optional)>
```

**Types:**
- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation
- `style` — Code formatting (no logic changes)
- `refactor` — Code restructuring (no logic changes)
- `perf` — Performance improvements
- `test` — Test additions/changes
- `chore` — Build, deps, configs

**Subject line:**
- Imperative mood ("add" not "added")
- Don't capitalize first letter
- No period at end
- Under 50 characters

**Body (optional):**
- Explain what and why, not how
- Wrap at 72 characters
- Separate from subject with blank line

## Database Migrations

If your changes modify database schema:

```bash
# Auto-generate migration
alembic revision --autogenerate -m "Add new_field to guests"

# Review the generated file in alembic/versions/

# Test the migration
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

**Migration rules:**
- ✅ Run `alembic upgrade head` successfully
- ✅ Include downgrade that cleanly rolls back
- ✅ Test upgrade and downgrade
- ✅ Commit migration with code changes

## Pull Request Process

### 1. Create PR

When pushing to GitHub, follow the prompt to create a pull request.

### 2. PR Title & Description

**Title:** Same as main commit message
```
feat: Add guest phone number validation in voice system
```

**Description:** Include:
- What problem does this solve?
- How does it solve the problem?
- Any breaking changes?
- Testing steps

**Template:**
```markdown
## Description
Adds phone number validation for voice system to prevent invalid calls.

## Type of Change
- [x] New feature
- [ ] Bug fix
- [ ] Documentation
- [ ] Breaking change

## Testing
1. Create guest with invalid phone: +123
2. Verify validation error shown
3. Create guest with valid phone: +14155551234
4. Verify success

## Checklist
- [x] Code follows style guide
- [x] Tests added/updated
- [x] Documentation updated
- [x] No breaking changes
```

### 3. Continuous Integration

GitHub Actions automatically run:
- Python tests (pytest)
- Linting (pylint, black)
- Type checking (mypy)
- Security scanning (pip-audit)

All checks must pass before merge.

### 4. Code Review

At least one maintainer must review and approve.

**Review feedback:**
- Be respectful and constructive
- Explain the reasoning
- Suggest improvements
- Ask clarifying questions

**Addressing feedback:**
- Make requested changes
- Push updated code (don't rebase)
- Mark as "ready for re-review"

### 5. Merge

After approval, PR is merged to main. Thanks! 🎉

## Reporting Bugs

### Security Issues

**Do NOT file public issues for security bugs.**

Email security@yourdomain.com with details.

### Regular Bugs

Use GitHub Issues with this template:

```markdown
## Description
Clear description of the bug.

## Reproduction
Steps to reproduce:
1. Go to ...
2. Click on ...
3. See error

## Expected Behavior
What should happen instead.

## Actual Behavior
What actually happens.

## Environment
- OS: macOS / Linux / Windows
- Browser: Chrome / Firefox / Safari
- App version: 1.0.0

## Screenshots
(if applicable)
```

## Documentation Contributions

Improvements to docs are very welcome!

### Updating Existing Docs

1. Edit the `.md` file in `/docs`
2. Keep formatting consistent
3. Update any related documentation
4. Submit PR with changes

### Adding New Docs

Create a new `.md` file in `/docs`:

**Template:**
```markdown
# Topic Name

## Overview
Brief description of topic.

## Key Concepts
- Concept 1 — Description
- Concept 2 — Description

## Example
Code example here

## Related Topics
- [Other doc](other.md)
```

## Testing Contributions

### Writing Tests

Tests should:
- Test one thing clearly
- Have descriptive names
- Include docstrings
- Be isolated (no dependencies between tests)

```python
def test_guest_creation_with_valid_data():
    """Test creating guest with complete valid data."""
    guest = create_guest(
        name="John Doe",
        phone="+14155551234",
        email="john@example.com"
    )
    assert guest.name == "John Doe"
    assert guest.phone == "+14155551234"
```

### Running Tests Locally

```bash
# All tests
pytest web/tests/ -v

# Specific file
pytest web/tests/test_voice.py -v

# Specific test
pytest web/tests/test_voice.py::test_incoming_call -v

# Tests matching pattern
pytest web/tests/ -k "voice" -v

# With coverage
pytest web/tests/ --cov=web --cov-report=html
```

## Feature Requests

Use GitHub Issues with template:

```markdown
## Feature Request
Brief description of desired feature.

## Use Case
Why is this needed?

## Proposed Solution
How should it work?

## Alternative Solutions
Other approaches considered.

## Related Issues
Any existing discussions?
```

## Project Structure

Understand the project before contributing:

- **`web/app.py`** — Main API routes (7000+ lines)
- **`web/models.py`** — Database models (ALL models defined here)
- **`web/integrations/`** — Third-party integrations
- **`web/services/`** — Business logic
- **`worker/`** — Background jobs
- **`frontend/`** — React UI
- **`docs/`** — Documentation

See **[DEVELOPMENT.md](DEVELOPMENT.md)** for detailed structure.

## Common Tasks

### Add New API Endpoint

1. Define Pydantic schema in `web/schemas.py`
2. Add SQLAlchemy model if needed to `web/models.py`
3. Add endpoint in `web/app.py`
4. Add tests in `web/tests/`
5. Document in `docs/API.md`

### Add New Integration

1. Create `web/integrations/service.py`
2. Implement API client
3. Add configuration to `.env.example`
4. Add tests in `web/tests/test_integrations.py`
5. Document setup in `docs/SETUP.md`

### Fix a Bug

1. Add failing test that reproduces bug
2. Fix the bug in the code
3. Verify test passes
4. Commit with `fix:` prefix
5. Submit PR

### Improve Performance

1. Profile to identify bottleneck
2. Implement optimization
3. Benchmark improvement
4. Add test to prevent regression
5. Document performance impact
6. Commit with `perf:` prefix

## Development Tips

### Find Something to Work On

- Check GitHub Issues for "good first issue"
- Pick something in the Roadmap (docs/ROADMAP.md)
- Ask in discussions

### Ask for Help

- Comment on the issue
- Ask in the discussion
- Email the maintainers
- Check existing documentation

### Keep Your Fork Updated

```bash
git fetch upstream
git rebase upstream/main
git push origin main
```

### Before Large Changes

- Open an issue first to discuss
- Get feedback from maintainers
- Ensure alignment before coding

## Release Process

Maintainers only:

```bash
# Update version
# Update CHANGELOG
# Run all tests
# Tag release
# Create GitHub release
# Deploy to production
```

## Code Ownership

- **Frontend**: React components in `/frontend`
- **Backend**: FastAPI in `/web`
- **Integrations**: Third-party services in `/web/integrations`
- **Voice**: Twilio/Deepgram/OpenRouter in `/web/integrations/voice.py`
- **Docs**: All markdown files in `/docs`

## Questions?

- Read **[DEVELOPMENT.md](DEVELOPMENT.md)** for code structure
- Read **[SETUP.md](SETUP.md)** for environment setup
- Check GitHub Discussions for common questions
- Email team@yourdomain.com for help

## Recognition

Contributors are:
- Credited in PR and commit history
- Added to CONTRIBUTORS.md (future)
- Mentioned in releases

Thank you for making HostAI better! 🚀
