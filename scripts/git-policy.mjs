#!/usr/bin/env node

import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const repoRoot = execFileSync('git', ['rev-parse', '--show-toplevel'], {
  encoding: 'utf8',
}).trim();

const allowedBranchPattern =
  /^(?:main|master|(?:feature|fix|docs|refactor|test|chore|release|hotfix|codex|dependabot|renovate)\/[A-Za-z0-9][A-Za-z0-9._/-]*)$/;
const conventionalCommitPattern =
  /^(feat|fix|docs|style|refactor|test|chore|build|ci|perf|revert)(\([^)]+\))?: .+/;
const specialCommitPrefixes = ['Merge ', 'Revert ', 'fixup! ', 'squash! '];
const maxSubjectLength = 100;

function runGit(args) {
  return execFileSync('git', args, {
    cwd: repoRoot,
    encoding: 'utf8',
  }).trim();
}

function currentBranch() {
  return runGit(['branch', '--show-current']);
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

function validateBranch(branch, { allowMainPush = false } = {}) {
  if (!branch) {
    fail('当前处于 detached HEAD，无法执行仓库 Git 规则检查。请切换到命名分支后再提交或推送。');
  }

  if (!allowedBranchPattern.test(branch)) {
    fail(
      `分支名 "${branch}" 不符合规则。请使用 feature/*、fix/*、docs/*、refactor/*、test/*、chore/*、release/*、hotfix/* 或 codex/*。`,
    );
  }

  if ((branch === 'main' || branch === 'master') && !allowMainPush) {
    fail(
      '当前分支是 main/master。仓库默认不允许直接在主分支提交或推送，请切换到工作分支；如确需发布维护，请显式设置 ALLOW_MAIN_PUSH=1。',
    );
  }
}

function validateCommitMessage(messageFile) {
  const rawMessage = readFileSync(messageFile, 'utf8').replace(/^\uFEFF/, '');
  const firstLine = rawMessage.split(/\r?\n/, 1)[0].trim();

  validateBranch(currentBranch(), { allowMainPush: process.env.ALLOW_MAIN_PUSH === '1' });

  if (!firstLine) {
    fail('提交信息为空。请使用 Conventional Commits，例如：feat(scope): subject');
  }

  if (specialCommitPrefixes.some((prefix) => firstLine.startsWith(prefix))) {
    return;
  }

  if (!conventionalCommitPattern.test(firstLine)) {
    fail(
      '提交信息不符合 Conventional Commits。请使用 type(scope): subject 形式，例如：fix(video): handle temp url fallback',
    );
  }

  if (firstLine.length > maxSubjectLength) {
    fail(`提交标题过长（${firstLine.length} 字符）。建议控制在 ${maxSubjectLength} 字符以内。`);
  }
}

function setupHooks() {
  runGit(['config', '--local', 'core.hooksPath', '.githooks']);
  const hooksPath = runGit(['config', '--local', '--get', 'core.hooksPath']);
  console.log(`已启用本地 hooksPath: ${hooksPath}`);
  console.log('commit-msg 与 pre-push 规则已生效。');
}

function printBranchPolicy() {
  const branch = currentBranch();
  validateBranch(branch, { allowMainPush: process.env.ALLOW_MAIN_PUSH === '1' });
  console.log(`当前分支 ${branch} 已通过 Git 规则检查。`);
}

const command = process.argv[2];
const arg = process.argv[3];

switch (command) {
  case 'commit-msg':
    if (!arg) {
      fail('缺少提交信息文件路径。');
    }
    validateCommitMessage(arg);
    break;
  case 'pre-push':
    validateBranch(currentBranch(), { allowMainPush: process.env.ALLOW_MAIN_PUSH === '1' });
    break;
  case 'setup-hooks':
    setupHooks();
    break;
  case 'branch':
    printBranchPolicy();
    break;
  default:
    fail(
      '用法: node scripts/git-policy.mjs <commit-msg|pre-push|setup-hooks|branch> [message-file]',
    );
}
