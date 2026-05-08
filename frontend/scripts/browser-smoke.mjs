import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const frontendDir = path.resolve(__dirname, "..");
const rootDir = path.resolve(frontendDir, "..");

const frontendUrl = process.env.LUMENX_E2E_FRONTEND_URL || "http://127.0.0.1:3000";
const backendUrl = process.env.LUMENX_E2E_BACKEND_URL || "http://127.0.0.1:18177";
const headless = process.env.LUMENX_E2E_HEADLESS !== "0";
const artifactDir = process.env.LUMENX_E2E_ARTIFACT_DIR || path.join(rootDir, "test-results");
const smokeImageBytes = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X2VINQAAAABJRU5ErkJggg==",
  "base64",
);

const smokeScenario = process.env.LUMENX_BROWSER_SMOKE_SCENARIO || "full";
const summaryPath = process.env.LUMENX_E2E_SUMMARY_PATH || path.join(artifactDir, `browser-smoke-${smokeScenario}.json`);
const smokeTitle = `Codex Browser Smoke ${Date.now()}`;
const smokeText = "本地浏览器烟雾测试：创建项目后打开分镜，再导入样板项目并检查分镜列表。";
const localVideoPrompt = "一位年轻女孩站在学校门口，人物清晰可见，抬手向镜头挥手，写实真人风格。";
const promptQualityPrompt = "快速移动";

const summary = {
  scenario: smokeScenario,
  status: "running",
  frontendUrl,
  backendUrl,
  startedAt: new Date().toISOString(),
  endedAt: null,
  projectIds: [],
  dialogMessages: [],
  lastEndpoint: null,
  screenshotPath: null,
  error: null,
};

function noteEndpoint(method, url, status) {
  summary.lastEndpoint = {
    method,
    url,
    status,
    recordedAt: new Date().toISOString(),
  };
}

function noteProjectId(projectId) {
  if (!projectId) return;
  if (!summary.projectIds.includes(projectId)) {
    summary.projectIds.push(projectId);
  }
}

async function saveSummary() {
  summary.endedAt = new Date().toISOString();
  await fs.mkdir(path.dirname(summaryPath), { recursive: true });
  await fs.writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, "utf-8");
}

function extractProjectId(url) {
  const hash = new URL(url).hash;
  const match = hash.match(/^#\/project\/([^/?#]+)/);
  return match?.[1] || null;
}

async function fetchProject(projectId) {
  const response = await fetch(`${backendUrl}/projects/${projectId}`);
  noteEndpoint("GET", `${backendUrl}/projects/${projectId}`, response.status);
  if (!response.ok) {
    throw new Error(`Failed to load project ${projectId}: HTTP ${response.status}`);
  }
  return response.json();
}

async function uploadFrameImage(projectId, frameId) {
  const formData = new FormData();
  formData.append("file", new Blob([smokeImageBytes], { type: "image/png" }), "browser-smoke.png");
  const response = await fetch(`${backendUrl}/projects/${projectId}/frames/${frameId}/upload_image`, {
    method: "POST",
    body: formData,
  });
  noteEndpoint("POST", `${backendUrl}/projects/${projectId}/frames/${frameId}/upload_image`, response.status);
  if (!response.ok) {
    throw new Error(`Failed to upload smoke frame image: HTTP ${response.status}`);
  }
  return response.json();
}

async function waitForCompletedVideoTask(projectId, taskId) {
  const deadline = Date.now() + 120000;
  let lastStatus = null;

  while (Date.now() < deadline) {
    const project = await fetchProject(projectId);
    const task = (project.video_tasks || []).find((item) => item.id === taskId);
    if (task) {
      lastStatus = task.status;
      if (task.status === "completed") {
        return task;
      }
      if (task.status === "failed") {
        throw new Error(`Video task ${taskId} failed`);
      }
    }
    await pageWait(1000);
  }

  throw new Error(`Timed out waiting for video task ${taskId}; last status=${lastStatus || "missing"}`);
}

async function waitForLatestVideoTaskId(projectId, previousTaskId = null) {
  const deadline = Date.now() + 30000;
  let lastCount = 0;

  while (Date.now() < deadline) {
    const project = await fetchProject(projectId);
    const tasks = project.video_tasks || [];
    lastCount = tasks.length;
    const latest = tasks[tasks.length - 1];
    if (latest?.id && latest.id !== previousTaskId) {
      return latest.id;
    }
    await pageWait(500);
  }

  throw new Error(`Timed out waiting for a video task to be created; observed count=${lastCount}`);
}

function pageWait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runStep(label, action) {
  console.log(`[browser-smoke] STEP: ${label}`);
  try {
    const result = await action();
    console.log(`[browser-smoke] OK: ${label}`);
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    throw new Error(`${label} failed: ${message}`);
  }
}

async function openCreateProjectDialog(page) {
  const dialog = page.getByTestId("create-project-dialog");
  const dropdown = page.getByTestId("home-create-dropdown-toggle");
  const emptyCard = page.getByTestId("home-empty-create-project-card");

  for (let attempt = 1; attempt <= 4; attempt += 1) {
    if (await dialog.isVisible().catch(() => false)) return;

    if (await dropdown.isVisible().catch(() => false)) {
      const option = page.getByTestId("home-create-project-option");
      if (!(await option.isVisible().catch(() => false))) {
        await dropdown.click({ timeout: 30000 });
        await option.waitFor({ state: "visible", timeout: 10000 });
      }
      await option.click({ timeout: 30000 });
    } else {
      await emptyCard.waitFor({ state: "visible", timeout: 30000 });
      await emptyCard.scrollIntoViewIfNeeded();
      await emptyCard.click({ timeout: 30000 });
    }

    const opened = await dialog.waitFor({ state: "visible", timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    if (opened) return;

    console.warn(`[browser-smoke] Create dialog did not open on attempt ${attempt}; retrying.`);
    await page.waitForTimeout(1000);
  }

  await dialog.waitFor({ state: "visible", timeout: 5000 });
}

async function openStoryboard(page) {
  await page.getByTestId("project-client").waitFor({ state: "visible", timeout: 60000 });
  await page.getByTestId("pipeline-step-storyboard").click();
  await page.getByTestId("storyboard-composer").waitFor({ state: "visible", timeout: 60000 });
}

async function openMotion(page) {
  await page.getByTestId("project-client").waitFor({ state: "visible", timeout: 60000 });
  await page.getByTestId("pipeline-step-motion").click();
  await page.getByTestId("video-creator").waitFor({ state: "visible", timeout: 60000 });
}

async function waitForTestIdCount(page, testId, minCount = 1, timeout = 30000) {
  const locator = page.getByTestId(testId);
  await locator.first().waitFor({ state: "visible", timeout });
  const deadline = Date.now() + timeout;
  let count = 0;
  while (Date.now() < deadline) {
    count = await locator.count();
    if (count >= minCount) {
      return count;
    }
    await pageWait(250);
  }
  throw new Error(`Timed out waiting for ${testId} count >= ${minCount}; observed ${count}`);
}

async function waitForEnabledTestId(page, testId, timeout = 30000) {
  await page.getByTestId(testId).waitFor({ state: "visible", timeout });
  await page.waitForFunction(
    (targetTestId) => {
      const element = document.querySelector(`[data-testid="${targetTestId}"]`);
      return Boolean(element && !element.hasAttribute("disabled"));
    },
    testId,
    { timeout },
  );
}

async function waitForVideoTaskPost(page) {
  const response = await page.waitForResponse(
    (item) => item.request().method() === "POST" && item.url().includes("/video_tasks"),
    { timeout: 30000 },
  );
  noteEndpoint(response.request().method(), response.url(), response.status());
  const responseText = await response.text().catch(() => "");
  if (!response.ok()) {
    throw new Error(`Video task POST failed with HTTP ${response.status()}: ${responseText.slice(0, 500)}`);
  }
  return response;
}

async function deleteProject(projectId) {
  if (!projectId) return;
  try {
    const response = await fetch(`${backendUrl}/projects/${projectId}`, { method: "DELETE" });
    if (!response.ok) {
      console.warn(`[browser-smoke] Cleanup delete for ${projectId} returned HTTP ${response.status}`);
    }
  } catch (error) {
    console.warn(`[browser-smoke] Cleanup skipped for project ${projectId}: ${error.message}`);
  }
}

async function captureFailure(page) {
  await fs.mkdir(artifactDir, { recursive: true });
  const screenshotPath = path.join(artifactDir, "browser-e2e-smoke-failure.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  summary.screenshotPath = screenshotPath;
  console.error(`[browser-smoke] Failure screenshot: ${screenshotPath}`);
}

async function waitForTextareaValue(page, testId, expectedValue, timeout = 10000) {
  await page.waitForFunction(
    ([targetTestId, value]) => {
      const element = document.querySelector(`[data-testid="${targetTestId}"]`);
      return Boolean(element && "value" in element && element.value === value);
    },
    [testId, expectedValue],
    { timeout },
  );
}

async function selectFixtureProject(page) {
  await page.goto(`${frontendUrl}#/`, { waitUntil: "commit", timeout: 60000 });
  await page.getByTestId("lumenx-home").waitFor({ state: "visible", timeout: 60000 });
  const fixtureCard = page.getByTestId("fixture-project-card-liuyi-that-day");
  await fixtureCard.waitFor({ state: "visible", timeout: 60000 });
  await fixtureCard.click();
  await page.waitForURL(/#\/project\//, { timeout: 90000 });
  await page.getByTestId("project-client").waitFor({ state: "visible", timeout: 60000 });
  const fixtureProjectId = extractProjectId(page.url());
  if (!fixtureProjectId) throw new Error("Fixture project URL did not include a project id");
  noteProjectId(fixtureProjectId);
  return fixtureProjectId;
}

async function createStandaloneProject(page) {
  await runStep("创建项目", async () => {
    await page.goto(`${frontendUrl}#/`, { waitUntil: "commit", timeout: 60000 });
    await page.getByTestId("lumenx-home").waitFor({ state: "visible", timeout: 60000 });
    await openCreateProjectDialog(page);
    await page.getByTestId("create-project-dialog").waitFor({ state: "visible", timeout: 30000 });
    await page.getByTestId("create-project-title-input").fill(smokeTitle);
    await page.getByTestId("create-project-script-input").fill(smokeText);
    await page.getByTestId("create-project-submit").click();
    await page.waitForURL(/#\/project\//, { timeout: 60000 });
  });

  const createdProjectId = extractProjectId(page.url());
  if (!createdProjectId) throw new Error("Created project URL did not include a project id");
  noteProjectId(createdProjectId);
  console.log(`[browser-smoke] Created project opened: ${createdProjectId}`);
  await runStep("打开项目并进入分镜", async () => {
    await openStoryboard(page);
  });
  return createdProjectId;
}

async function importFixtureProject(page) {
  return runStep("导入样板", async () => {
    const fixtureProjectId = await selectFixtureProject(page);
    if (!fixtureProjectId) throw new Error("Fixture project URL did not include a project id");
    console.log(`[browser-smoke] Fixture project imported: ${fixtureProjectId}`);
    return fixtureProjectId;
  });
}

async function prepareFixtureStoryboard(page, fixtureProjectId) {
  return runStep("打开分镜", async () => {
    const fixtureProject = await fetchProject(fixtureProjectId);
    const firstFrame = fixtureProject.frames?.[0];
    if (!firstFrame) throw new Error("Imported fixture project has no storyboard frames");

    const uploadedFixture = await uploadFrameImage(fixtureProjectId, firstFrame.id);
    const uploadedFrame = uploadedFixture.frames?.find((frame) => frame.id === firstFrame.id);
    if (!uploadedFrame?.rendered_image_url && !uploadedFrame?.image_url) {
      throw new Error("Frame upload did not return a usable storyboard image");
    }

    await page.reload({ waitUntil: "commit", timeout: 60000 });
    await page.getByTestId("project-client").waitFor({ state: "visible", timeout: 60000 });
    await openStoryboard(page);
    const storyboardFrames = await waitForTestIdCount(page, "storyboard-frame-card", 1, 60000);
    if (storyboardFrames <= 0) throw new Error("Imported fixture project opened without storyboard frames");
    console.log(`[browser-smoke] Fixture storyboard frames visible: ${storyboardFrames}`);
  });
}

async function triggerVideoTask(page, fixtureProjectId, promptText) {
  return runStep("触发本地视频任务", async () => {
    await openMotion(page);
    const videoFrames = page.locator('[data-testid="video-storyboard-frame-card"]');
    await videoFrames.first().waitFor({ state: "visible", timeout: 60000 });
    const videoFrameCount = await videoFrames.count();
    if (videoFrameCount <= 0) throw new Error("Video creator opened without storyboard frames");
    await videoFrames.first().click();
    await page.getByTestId("prompt-builder-textarea").fill(promptText);
    await waitForTextareaValue(page, "prompt-builder-textarea", promptText);
    const preSubmitProject = await fetchProject(fixtureProjectId);
    const previousTaskId = preSubmitProject.video_tasks && preSubmitProject.video_tasks.length > 0
      ? preSubmitProject.video_tasks[preSubmitProject.video_tasks.length - 1].id
      : null;
    await waitForEnabledTestId(page, "video-submit", 30000);
    const videoTaskPost = waitForVideoTaskPost(page);
    await page.getByTestId("video-submit").click();
    await videoTaskPost;
    const createdTaskId = await waitForLatestVideoTaskId(fixtureProjectId, previousTaskId);
    const createdTask = await waitForCompletedVideoTask(fixtureProjectId, createdTaskId);
    if (!createdTask.video_url || !createdTask.video_url.startsWith("video/")) {
      throw new Error(`Local video task did not persist a local output path: ${createdTask.video_url || "missing"}`);
    }
    console.log(`[browser-smoke] Local video task completed: ${createdTask.id}`);
  });
}

async function triggerPromptQualityGate(page, fixtureProjectId) {
  return runStep("触发提示词质量门禁", async () => {
    await openMotion(page);
    const videoFrames = page.locator('[data-testid="video-storyboard-frame-card"]');
    await videoFrames.first().waitFor({ state: "visible", timeout: 60000 });
    await videoFrames.first().click();
    await page.getByTestId("prompt-builder-textarea").fill(promptQualityPrompt);
    await waitForTextareaValue(page, "prompt-builder-textarea", promptQualityPrompt);
    const preSubmitProject = await fetchProject(fixtureProjectId);
    const previousTaskCount = preSubmitProject.video_tasks?.length || 0;
    await waitForEnabledTestId(page, "video-submit", 30000);
    const noVideoTaskRequest = page.waitForRequest(
      (item) => item.method() === "POST" && item.url().includes("/video_tasks"),
      { timeout: 5000 },
    ).then(() => true).catch(() => false);
    await page.getByTestId("video-submit").click();
    await page.waitForTimeout(1000);
    const requestSeen = await noVideoTaskRequest;
    if (requestSeen) {
      throw new Error("Prompt quality gate should block submission before any video task POST is sent");
    }
    const latestProject = await fetchProject(fixtureProjectId);
    const latestTaskCount = latestProject.video_tasks?.length || 0;
    if (latestTaskCount !== previousTaskCount) {
      throw new Error(`Prompt quality gate should not create video tasks; before=${previousTaskCount}, after=${latestTaskCount}`);
    }
    const dialogMessage = summary.dialogMessages.join("\n");
    if (!dialogMessage.includes("主体不清") && !dialogMessage.includes("提示词存在阻断项")) {
      throw new Error(`Prompt quality gate did not surface the expected alert; dialogs=${dialogMessage || "none"}`);
    }
    console.log("[browser-smoke] Prompt quality gate blocked the ambiguous prompt as expected.");
  });
}

async function main() {
  const browser = await chromium.launch({ headless });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();

  page.on("dialog", async (dialog) => {
    const message = dialog.message();
    summary.dialogMessages.push(message);
    console.warn(`[browser-smoke] Dialog accepted: ${message}`);
    await dialog.accept();
  });
  page.on("response", (response) => {
    if (response.url().startsWith(backendUrl)) {
      noteEndpoint(response.request().method(), response.url(), response.status());
    }
  });
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      console.warn(`[browser-smoke] Browser ${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    console.error(`[browser-smoke] Page error: ${error.message}`);
  });

  try {
    console.log(`[browser-smoke] Scenario: ${smokeScenario}`);
    if (smokeScenario === "create") {
      await createStandaloneProject(page);
    } else if (smokeScenario === "import") {
      await importFixtureProject(page);
    } else if (smokeScenario === "storyboard") {
      const fixtureProjectId = await importFixtureProject(page);
      await prepareFixtureStoryboard(page, fixtureProjectId);
    } else if (smokeScenario === "video") {
      const fixtureProjectId = await importFixtureProject(page);
      await prepareFixtureStoryboard(page, fixtureProjectId);
      await triggerVideoTask(page, fixtureProjectId, localVideoPrompt);
    } else if (smokeScenario === "prompt-quality") {
      const fixtureProjectId = await importFixtureProject(page);
      await prepareFixtureStoryboard(page, fixtureProjectId);
      await triggerPromptQualityGate(page, fixtureProjectId);
    } else if (smokeScenario === "full") {
      await createStandaloneProject(page);
      const fixtureProjectId = await importFixtureProject(page);
      await prepareFixtureStoryboard(page, fixtureProjectId);
      await triggerVideoTask(page, fixtureProjectId, localVideoPrompt);
    } else {
      throw new Error(`Unknown browser smoke scenario: ${smokeScenario}`);
    }

    summary.status = "passed";
    console.log(`[browser-smoke] Scenario ${smokeScenario} passed.`);
  } catch (error) {
    summary.status = "failed";
    summary.error = error instanceof Error ? error.message : String(error);
    await captureFailure(page);
    throw error;
  } finally {
    try {
      for (const projectId of summary.projectIds) {
        await deleteProject(projectId);
      }
      await context.close();
      await browser.close();
    } finally {
      await saveSummary();
      console.log(`[browser-smoke] Summary: ${summaryPath}`);
    }
  }
}

main().catch((error) => {
  console.error("[browser-smoke] Frontend browser smoke failed:", error);
  process.exit(1);
});
