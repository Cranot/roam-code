import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import prComment from '../.github/scripts/pr-comment.js';

for (const [name, summary, expected, score] of [
  ['unavailable comparison', { state: 'comparison_unavailable', risk_score: null }, 'PR comparison could not run.'],
  ['failed analysis', { state: 'git_error', verdict: 'Could not read changed files' }, 'Could not read changed files'],
  ['unscored result', { risk_score: null, verdict: 'Risk evidence is incomplete' }, 'Risk evidence is incomplete'],
  ['empty comparison', { state: 'no_changes', risk_score: 0, verdict: 'No changed files' }, 'No changed files', 0],
  ['scored result', { risk_score: 21, verdict: 'Low risk' }, 'Low risk', 21],
]) {
  test(`PR Risk comment renders ${name}`, async (t) => {
    const resultsDir = await mkdtemp(join(tmpdir(), 'roam-pr-comment-'));
    t.after(() => rm(resultsDir, { recursive: true, force: true }));
    await writeFile(join(resultsDir, 'pr-risk.json'), JSON.stringify({ summary }));

    const comment = await prComment.buildCommentAsync({ RESULTS_DIR: resultsDir });
    const [visibleComment] = comment.split('<details>');
    const riskSection = visibleComment.split('### PR Risk\n\n')[1]?.split('\n### ')[0];
    assert.ok(riskSection, comment);
    assert.equal(riskSection.includes('PR comparison could not run.'), summary.state === 'comparison_unavailable');
    assert.ok(riskSection.includes(expected), riskSection);
    if (score === undefined) {
      assert.ok(!riskSection.includes('| Risk Score |'));
    } else {
      assert.ok(riskSection.includes(`| Risk Score | ${score}/100 |`));
    }
  });
}
