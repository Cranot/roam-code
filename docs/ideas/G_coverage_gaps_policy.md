# Idea G: Coverage-Gaps Policy-as-Code

## Origin
chuckjewell fork -- coverage-gaps with manual --gate flags.
The idea is to auto-detect frameworks + support policy config files.

## Why
Current `roam coverage-gaps` requires manual `--gate requireAuth`. Nobody remembers
every gate symbol. Framework-aware auto-detection + policy files make it zero-config.

## Research Findings

### Gate Patterns by Framework (12 frameworks mapped)

**Express:** requireAuth, isAuthenticated, passport.authenticate, verifyToken
**Django:** login_required, permission_required, LoginRequiredMixin, IsAuthenticated
**Flask:** login_required, jwt_required, auth_required
**FastAPI:** Depends(get_current_user), OAuth2PasswordBearer
**Spring:** @PreAuthorize, @Secured, @RolesAllowed
**Rails:** authenticate_user!, authorize, load_and_authorize_resource
**ASP.NET:** [Authorize], RequireAuthorization
**Gin/Chi:** AuthRequired, JWTAuth, AuthMiddleware
**Laravel:** ->middleware('auth'), Gate::allows, $this->authorize
**Symfony:** #[IsGranted], denyAccessUnlessGranted
**Next.js:** getServerSession, withAuth, middleware.ts
**JAX-RS:** @RolesAllowed, @PermitAll

### Framework Auto-Detection
Scan dependency manifests:
- package.json -> express, next, etc.
- requirements.txt/pyproject.toml -> django, flask, fastapi
- pom.xml/build.gradle -> spring-security
- Gemfile -> rails, devise, pundit
- go.mod -> gin, chi
- composer.json -> laravel, symfony
- *.csproj -> Microsoft.AspNetCore

### Policy Config: `.roam-gates.yml`
```yaml
version: "1"
frameworks: [express, django]  # auto-detected if omitted

gates:
  custom:
    - name: requireAuth
    - pattern: "auth|permission|guard|protect"

exempt:
  names: [healthCheck, login, register, webhook]
  patterns: ["^health", "^public", "^webhook_"]
  paths: ["src/routes/public/**", "**/webhooks/**"]
  annotations: [AllowAnonymous, PermitAll]

policy:
  min_coverage_pct: 95
  max_depth: 8
  default_posture: deny
```

### Implementation Plan
1. Create `src/roam/commands/gate_presets.py` with per-framework gate dicts
2. Add framework auto-detection in `_detect_frameworks()` (scan manifests)
3. Add `.roam-gates.yml` loading with validation
4. Merge: CLI flags > config file > auto-detected presets
5. Add exemption filtering to results
6. Add `--auto` flag for fully automatic mode

### Our Advantage
roam's BFS call-graph reachability catches gates applied through middleware chains,
base class protections, and multi-layer calls. Semgrep only checks single files.
CodeQL needs full DB build. We're the sweet spot: graph-based but lightweight.

## Priority: MEDIUM (Tier 2)
## Effort: Medium
## Files touched: cmd_coverage_gaps.py (extend), new gate_presets.py
