const fs = require("fs");
const os = require("os");
const path = require("path");
const vscode = require("vscode");

const MAX_BYTES = 1024 * 1024;
const lastAt = new Map();

function config() {
  return vscode.workspace.getConfiguration("slackOffPolice");
}

function logPath() {
  const custom = config().get("logPath");
  if (custom) {
    return custom;
  }
  const home = process.env.SLACK_OFF_POLICE_HOME || path.join(os.homedir(), ".slack-off-police");
  return path.join(home, "vscode-activity.jsonl");
}

// ログが太り続けると監視側の読み込みが重くなるので、上限を超えたら後半だけ残す
function rotate(file) {
  try {
    if (fs.statSync(file).size <= MAX_BYTES) {
      return;
    }
    const lines = fs.readFileSync(file, "utf8").split("\n");
    fs.writeFileSync(file, lines.slice(Math.floor(lines.length / 2)).join("\n"), "utf8");
  } catch (err) {
    // ローテーションに失敗しても記録自体は続ける
  }
}

function append(event) {
  const file = logPath();
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    rotate(file);
    fs.appendFileSync(file, JSON.stringify(event) + "\n", "utf8");
  } catch (err) {
    // エディタ側の操作を止めないため、失敗は握りつぶす
  }
}

function fileOf(document) {
  if (!document || !document.uri || document.uri.scheme !== "file") {
    return "";
  }
  return document.uri.fsPath;
}

// 記録するのはパスと種別だけ。ファイルの中身やチャット本文は一切書き出さない
function record(type, document) {
  if (!config().get("enabled")) {
    return;
  }
  const file = fileOf(document);
  const key = type + "|" + file;
  const now = Date.now();
  const throttleMs = Math.max(0, Number(config().get("throttleSeconds")) || 0) * 1000;
  if (now - (lastAt.get(key) || 0) < throttleMs) {
    return;
  }
  lastAt.set(key, now);

  const folder = document ? vscode.workspace.getWorkspaceFolder(document.uri) : undefined;
  append({
    ts: now,
    type: type,
    file: file,
    lang: document ? document.languageId : undefined,
    workspace: folder ? folder.uri.fsPath : undefined,
  });
}

function activate(context) {
  const on = [
    vscode.window.onDidChangeActiveTextEditor((editor) => record("open", editor && editor.document)),
    vscode.workspace.onDidOpenTextDocument((document) => record("open", document)),
    vscode.window.onDidChangeTextEditorSelection((e) => record("read", e.textEditor.document)),
    vscode.window.onDidChangeTextEditorVisibleRanges((e) => record("read", e.textEditor.document)),
    vscode.workspace.onDidChangeTextDocument((e) => record("edit", e.document)),
    vscode.workspace.onDidSaveTextDocument((document) => record("save", document)),
    vscode.window.onDidChangeWindowState((state) => {
      if (state.focused) {
        record("focus", undefined);
      }
    }),
    vscode.commands.registerCommand("slackOffPolice.showLog", () =>
      vscode.window.showTextDocument(vscode.Uri.file(logPath()))
    ),
  ];

  // ノートブック（.ipynb）は onDidChangeActiveTextEditor に乗らないので別途拾う
  if (vscode.window.onDidChangeActiveNotebookEditor) {
    on.push(
      vscode.window.onDidChangeActiveNotebookEditor((editor) =>
        record("open", editor && editor.notebook)
      )
    );
  }

  context.subscriptions.push(...on);
  record("start", undefined);
}

function deactivate() {}

module.exports = { activate, deactivate };
