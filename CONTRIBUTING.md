# Contributing to LumenX Studio

Thank you for your interest in contributing to LumenX Studio! We welcome contributions from the community.

## 🌟 Ways to Contribute

- **Bug Reports**: Submit detailed bug reports via [GitHub Issues](https://github.com/alibaba/lumenx/issues)
- **Feature Requests**: Propose new features through GitHub Issues
- **Code Contributions**: Submit pull requests for bug fixes or new features
- **Documentation**: Improve documentation, tutorials, or examples
- **Community Support**: Help others in GitHub Discussions

## 🚀 Getting Started

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/lumenx.git
cd lumenx

# Add upstream remote
git remote add upstream https://github.com/alibaba/lumenx.git
```

### 2. Set Up Development Environment

Follow the instructions in [README_EN.md](README_EN.md#quick-start) to set up your local environment.

### 3. Create a Feature Branch

```bash
# Update your main branch
git checkout main
git pull upstream main

# Create a new branch for your work
git checkout -b feature/your-feature-name
```

**Branch Naming Convention**:
- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring
- `test/description` - Test additions or updates

## 🧭 Media Storage & Provider Routing Rules

When contributing to media upload/generation flows, please keep these invariants:

- **Local-first storage**: files under `output/` are always written first and remain the durable project source.
- **OSS is optional**: OSS acts as an optional mirror and signed-URL service, not a mandatory storage backend.
- **DashScope-first backend**: for supported model families, DashScope is the default provider backend.
- **Vendor-direct remains available**: Kling/Vidu vendor APIs are still supported when users opt in and configure vendor credentials.

Use the following vocabulary consistently in PRs, code, and docs:

- `storage_mode`: `local_only` or `local_plus_oss`
- `provider_backend`: `dashscope` or `vendor`
- `media_ref`: stable project-side media reference (for example local relative path or OSS object key)
- `resolved_media_input`: request-side provider-ready payload derived from `media_ref`

## 🧠 Model Onboarding Workflow

When your change touches model support, do not treat it as an ordinary string-replacement task. Use the repo-native model workflow entry:

- textual alias: `/lumenx-model-onboarding`
- workflow docs:
  - `.codex/workflows/lumenx-model-onboarding.md`
  - `.claude/commands/lumenx-model-onboarding.md`

Typical use cases:

- onboarding a new model ID
- updating model versions
- changing default models
- changing model parameter metadata
- changing model UI visibility
- refreshing vendor-doc evidence

Minimum expected command sequence after catalog changes:

```bash
python scripts/build_model_catalog.py
python scripts/validate_model_catalog.py
pytest -q
cd frontend && npm run typecheck
cd frontend && npm run test:all
cd frontend && npm run build
```

Read [docs/2-model-catalog-design/model-onboarding-implementation.md](docs/2-model-catalog-design/model-onboarding-implementation.md) before large model-support changes. It explains exactly which files own what.

## 📝 Code Style Guidelines

### Python Code (Backend)

We follow **PEP 8** style guide:

```bash
# Format code with black
black src/

# Check with flake8
flake8 src/
```

**Key Points**:
- Line length: 100 characters
- Use type hints for function signatures
- Write docstrings for all public functions and classes
- Use descriptive variable names

**Example**:
```python
def generate_storyboard(script: str, style: str = "default") -> List[Frame]:
    """Generate storyboard frames from script.
    
    Args:
        script: The input script text
        style: Visual style preset (default: "default")
        
    Returns:
        List of Frame objects with image URLs
    """
    # Implementation here
    pass
```

### TypeScript/React Code (Frontend)

We use **ESLint** configuration:

```bash
cd frontend
npm run lint
npm run type-check
```

**Key Points**:
- Use functional components with TypeScript
- Prefer named exports over default exports
- Use `async/await` over promises
- Add meaningful comments for complex logic

**Example**:
```typescript
interface StoryboardProps {
  frames: Frame[];
  onFrameSelect: (frameId: string) => void;
}

export function StoryboardEditor({ frames, onFrameSelect }: StoryboardProps) {
  // Component implementation
}
```

## 💬 Commit Message Convention

We use **Conventional Commits** format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only changes
- `style`: Code style changes (formatting, no logic change)
- `refactor`: Code restructuring without changing functionality
- `test`: Adding or updating tests
- `chore`: Build process or auxiliary tool changes

**Examples**:
```
feat(storyboard): add drag-and-drop frame reordering

fix(video): resolve FFmpeg concurrent processing issue

docs(readme): update installation instructions for Windows
```

## 🔄 Pull Request Process

### 1. Ensure Quality

Before submitting your PR:

```bash
# Run tests
pytest  # Python tests
cd frontend && npm test  # Frontend tests

# Check linting
black --check src/
flake8 src/
cd frontend && npm run lint

# Verify builds
./build_mac.sh  # or build_windows.ps1 on Windows
```

### 2. Update Documentation

- Update README if you've changed functionality
- Add/update docstrings and comments
- Update USER_MANUAL.md if user-facing features changed

### 3. Submit Pull Request

1. Push your branch to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

2. Open a Pull Request on GitHub:
   - Use a clear, descriptive title
   - Reference related issues (e.g., "Fixes #123")
   - Provide detailed description of changes
   - Include screenshots/GIFs for UI changes

3. Respond to review feedback:
   - Address comments promptly
   - Push additional commits to the same branch
   - Request re-review when ready

### 4. PR Review Checklist

Your PR should:
- [ ] Follow code style guidelines
- [ ] Include tests for new features/fixes
- [ ] Update relevant documentation
- [ ] Pass all CI checks
- [ ] Have a clear commit history
- [ ] Be focused on a single concern

## 🧪 Testing Guidelines

### Python Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_pipeline.py

# Run with coverage
pytest --cov=src tests/
```

### Frontend Tests

```bash
cd frontend

# Run tests
npm test

# Run tests with coverage
npm test -- --coverage
```

### Manual Testing

Before submitting:
1. Test the full workflow (Script → Assets → Storyboard → Video → Export)
2. Verify UI responsiveness on different screen sizes
3. Check error handling and edge cases

## 🐛 Bug Reports

When reporting bugs, please include:

1. **Environment**:
   - OS and version
   - Python version
   - Node.js version
   - Browser (for frontend issues)

2. **Steps to Reproduce**:
   - Detailed step-by-step instructions
   - Sample data if applicable

3. **Expected vs. Actual Behavior**:
   - What you expected to happen
   - What actually happened

4. **Screenshots/Logs**:
   - Error messages
   - Screenshots of UI issues
   - Relevant log excerpts

## 💡 Feature Requests

For feature requests, please describe:

1. **Use Case**: What problem does this solve?
2. **Proposed Solution**: How should it work?
3. **Alternatives**: Other approaches you've considered
4. **Additional Context**: Mockups, examples from other tools

## 📜 Code of Conduct

### Our Standards

- Be respectful and inclusive
- Welcome diverse perspectives
- Focus on constructive feedback
- Prioritize the project's best interests

### Unacceptable Behavior

- Harassment or discriminatory language
- Personal attacks or trolling
- Publishing others' private information
- Other conduct inappropriate in a professional setting

## 📞 Questions?

- **Technical Questions**: [GitHub Discussions](https://github.com/alibaba/lumenx/discussions)
- **Security Issues**: Email security@alibaba-inc.com (do not file public issues)

## 📄 License

By contributing to LumenX Studio, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

Thank you for contributing to LumenX Studio! 🎉
