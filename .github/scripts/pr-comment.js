// PR comment generator for roam-code GitHub Action.
// Reads analysis JSON output, formats a markdown comment, and
// upserts a single sticky PR comment using a marker.

const fs = require('fs');
const path = require('path');

const MARKER = '<!-- roam-code-analysis -->';
const MAX_COMMENT_CHARS = 65000;
const MAX_JSON_PER_COMMAND = 12000;

function _loadResults(resultsDir) {
  const results = {};
  if (!resultsDir || !fs.existsSync(resultsDir)) {
    return results;
  }

  for (const file of fs.readdirSync(resultsDir)) {
    if (!file.endsWith('.json')) continue;
    try {
      const raw = fs.readFileSync(path.join(resultsDir, file), 'utf8');
      const data = JSON.parse(raw);
      const cmd = file.replace('.json', '');
      results[cmd] = data;
    } catch (_e) {
      // Skip malformed JSON.
    }
  }
  return results;
}

function _severityLabel(score) {
  if (score >= 80) return 'HEALTHY';
  if (score >= 60) return 'FAIR';
  if (score >= 40) return 'NEEDS ATTENTION';
  return 'CRITICAL';
}

function clampComment(text, maxChars = MAX_COMMENT_CHARS) {
  if (!text || text.length <= maxChars) return text;
  const suffix = '\n\n...(comment truncated to fit GitHub size limit)\n';
  const headLen = Math.max(0, maxChars - suffix.length);
  return text.slice(0, headLen) + suffix;
}

function buildComment(env = process.env) {
  const resultsDir = env.RESULTS_DIR || '';
  const healthScore = env.HEALTH_SCORE || '';
  const gateExpr = env.GATE_EXPR || '';
  const gatePassed = env.GATE_PASSED || '';
  const commandsRun = env.COMMANDS_RUN || 'health';
  const changedOnly = env.CHANGED_ONLY || 'false';
  const baseRef = env.BASE_REF || '';
  const affectedCount = env.AFFECTED_COUNT || '';
  const sarifCategory = env.SARIF_CATEGORY || '';
  const sarifTruncated = env.SARIF_TRUNCATED || 'false';
  const sarifResults = env.SARIF_RESULTS || '';

  const results = _loadResults(resultsDir);

  const lines = [MARKER, '## roam-code Analysis', ''];

  if (changedOnly === 'true') {
    const scopeBits = [];
    if (baseRef) scopeBits.push(`base \`${baseRef}\``);
    if (affectedCount) scopeBits.push(`${affectedCount} changed+dependent files`);
    const scope = scopeBits.length > 0 ? scopeBits.join(', ') : 'incremental scope';
    lines.push(`**Mode:** incremental (\`changed-only\`) — ${scope}`, '');
  }

  if (healthScore) {
    const score = parseInt(healthScore, 10);
    if (Number.isFinite(score)) {
      lines.push(`**Health Score: ${score}/100** \`${_severityLabel(score)}\``, '');
    }
  }

  const verdicts = [];
  for (const cmd of Object.keys(results).sort()) {
    const data = results[cmd];
    const verdict = (data.summary && data.summary.verdict) || '';
    if (verdict) verdicts.push(`**${cmd}:** ${verdict}`);
  }
  if (verdicts.length > 0) {
    lines.push(...verdicts, '');
  }

  if (results.health && results.health.summary) {
    const s = results.health.summary;
    lines.push('### Health Metrics', '');
    lines.push('| Metric | Value |', '|--------|-------|');
    if (s.health_score !== undefined) lines.push(`| Health Score | ${s.health_score}/100 |`);
    if (s.tangle_ratio !== undefined) lines.push(`| Tangle Ratio | ${s.tangle_ratio}% |`);
    if (s.propagation_cost !== undefined) lines.push(`| Propagation Cost | ${s.propagation_cost} |`);
    if (s.issue_count !== undefined) lines.push(`| Total Issues | ${s.issue_count} |`);
    if (s.severity) {
      if (s.severity.CRITICAL) lines.push(`| Critical Issues | ${s.severity.CRITICAL} |`);
      if (s.severity.WARNING) lines.push(`| Warnings | ${s.severity.WARNING} |`);
    }
    lines.push('');
  }

  if (results['pr-risk'] && results['pr-risk'].summary) {
    const s = results['pr-risk'].summary;
    lines.push('### PR Risk', '');
    lines.push('| Metric | Value |', '|--------|-------|');
    if (s.risk_score !== undefined) lines.push(`| Risk Score | ${s.risk_score}/100 |`);
    if (s.files_changed !== undefined) lines.push(`| Files Changed | ${s.files_changed} |`);
    if (s.symbols_affected !== undefined) lines.push(`| Symbols Affected | ${s.symbols_affected} |`);
    lines.push('');
  }

  if (gateExpr) {
    const status = gatePassed === 'false' ? 'FAILED' : 'PASSED';
    lines.push(`### Quality Gate: ${status}`, '');
    lines.push(`Gate expression: \`${gateExpr}\``, '');
  }

  if (sarifCategory || sarifResults || sarifTruncated === 'true') {
    lines.push('### SARIF Upload', '');
    lines.push('| Metric | Value |', '|--------|-------|');
    if (sarifCategory) lines.push(`| Category | \`${sarifCategory}\` |`);
    if (sarifResults) lines.push(`| Results Uploaded | ${sarifResults} |`);
    if (sarifTruncated === 'true') lines.push('| Guardrails Truncated Results | yes |');
    lines.push('');
  }

  if (Object.keys(results).length > 0) {
    lines.push('<details>', '<summary>Full analysis output</summary>', '');
    for (const cmd of Object.keys(results).sort()) {
      const data = results[cmd];
      const json = JSON.stringify(data, null, 2);
      const clipped = json.length > MAX_JSON_PER_COMMAND
        ? `${json.substring(0, MAX_JSON_PER_COMMAND)}\n...(truncated)`
        : json;
      lines.push(`#### ${cmd}`, '', '```json', clipped, '```', '');
    }
    lines.push('</details>', '');
  }

  lines.push(
    '---',
    `*[roam-code](https://github.com/cosmohac/roam-code) analysis | Commands: \`${commandsRun}\`*`,
  );

  return clampComment(lines.join('\n'));
}

function selectStickyComments(comments = []) {
  const sticky = comments.filter(c => c.body && c.body.includes(MARKER));
  if (sticky.length === 0) {
    return { primary: null, duplicates: [] };
  }

  sticky.sort((a, b) => {
    const ta = Date.parse(a.updated_at || a.created_at || 0) || 0;
    const tb = Date.parse(b.updated_at || b.created_at || 0) || 0;
    if (tb !== ta) return tb - ta;
    return (b.id || 0) - (a.id || 0);
  });

  const [primary, ...duplicates] = sticky;
  return { primary, duplicates };
}

async function _listAllComments({ github, owner, repo, issue_number }) {
  const params = { owner, repo, issue_number, per_page: 100 };

  if (typeof github.paginate === 'function') {
    return github.paginate(github.rest.issues.listComments, params);
  }

  const all = [];
  let page = 1;
  while (true) {
    const { data } = await github.rest.issues.listComments({ ...params, page });
    all.push(...data);
    if (!Array.isArray(data) || data.length < 100) break;
    page += 1;
    if (page > 20) break;
  }
  return all;
}

async function upsertStickyComment({ github, context, core, body }) {
  const owner = context.repo.owner;
  const repo = context.repo.repo;
  const issue_number = context.issue.number;

  const comments = await _listAllComments({ github, owner, repo, issue_number });
  const { primary, duplicates } = selectStickyComments(comments);

  if (primary) {
    await github.rest.issues.updateComment({
      owner,
      repo,
      comment_id: primary.id,
      body,
    });
    core.info(`Updated existing PR comment #${primary.id}`);
  } else {
    const { data } = await github.rest.issues.createComment({
      owner,
      repo,
      issue_number,
      body,
    });
    core.info(`Created new PR comment #${data && data.id ? data.id : ''}`.trim());
  }

  for (const dup of duplicates) {
    try {
      await github.rest.issues.deleteComment({
        owner,
        repo,
        comment_id: dup.id,
      });
      core.info(`Removed duplicate sticky PR comment #${dup.id}`);
    } catch (err) {
      core.warning(`Could not remove duplicate sticky comment #${dup.id}: ${err.message}`);
    }
  }
}

async function handler({ github, context, core }) {
  const body = buildComment(process.env);
  await upsertStickyComment({ github, context, core, body });
}

module.exports = handler;
module.exports.handler = handler;
module.exports.MARKER = MARKER;
module.exports.buildComment = buildComment;
module.exports.clampComment = clampComment;
module.exports.selectStickyComments = selectStickyComments;
module.exports.upsertStickyComment = upsertStickyComment;
