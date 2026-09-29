import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const [artifactModule, jsonPath, xlsxPath, previewPath] = process.argv.slice(2);
if (!artifactModule || !jsonPath || !xlsxPath) {
  throw new Error(
    "usage: export_paper_results_summary.mjs ARTIFACT_MODULE INPUT.json OUTPUT.xlsx [PREVIEW.png]",
  );
}

const { SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactModule).href);
const payload = JSON.parse(await fs.readFile(jsonPath, "utf8"));
const session = payload.session ?? {};
const corners = payload.corners ?? [];
const files = payload.files ?? [];
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Results summary");

const red = "#A61B1B";
const darkRed = "#7F1D1D";
const paleRed = "#FDECEC";
const paleGray = "#F3F4F6";
const green = "#E7F5EA";
const text = "#202124";
const white = "#FFFFFF";
const font = "Arial";

sheet.showGridLines = false;
sheet.tabColor = red;
sheet.getRange("A2:O2").format.borders = {
  bottom: { style: "medium", color: red },
};
sheet.getRange("A2").values = [["Live maze test — results summary"]];
sheet.getRange("A2").format.font = { name: font, size: 15, bold: true, color: darkRed };
sheet.getRange("A3").values = [[
  `Subject ${session.subject ?? ""} · Test ${session.test ?? ""} · ` +
  `Standard maze ${session.maze_number ?? ""} (seed ${session.maze_seed ?? ""})`,
]];
sheet.getRange("A3").format.font = { name: font, size: 10, italic: true, color: "#5F6368" };

sheet.getRange("A5:B5").values = [["Session", "Value"]];
sheet.getRange("D5:E5").values = [["Result", "Value"]];
for (const address of ["A5:B5", "D5:E5"]) {
  sheet.getRange(address).format = {
    fill: red,
    font: { name: font, size: 10, bold: true, color: white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: darkRed },
  };
}

function wallClockDate(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
  if (!match) return null;
  const [, year, month, day, hour, minute, second] = match.map(Number);
  // Artifact-tool serializes UTC components into Excel. Using Date.UTC keeps
  // the operator's recorded local wall-clock fields from shifting by timezone.
  return new Date(Date.UTC(year, month - 1, day, hour, minute, second));
}
const started = wallClockDate(session.start_time);
const ended = wallClockDate(session.end_time);
sheet.getRange("A6:B13").values = [
  ["Subject ID", session.subject ?? ""],
  ["Test ID", session.test ?? ""],
  ["Decoder", session.decoder ?? ""],
  ["Standard maze", Number(session.maze_number ?? 0)],
  ["Frozen seed", Number(session.maze_seed ?? 0)],
  ["Session started", started],
  ["Session ended", ended],
  ["Wall-clock duration (s)", Number(session.wall_duration_s ?? 0)],
];
sheet.getRange("D6:E15").values = [
  ["Completed", session.completed ? "Yes" : "No / partial"],
  ["Scored corners", Number(session.decisions ?? 0)],
  ["Correct", session.correct == null ? null : Number(session.correct)],
  ["Wrong", session.wrong == null ? null : Number(session.wrong)],
  ["Accuracy", null],
  ["Unscored correction turns", Number(session.recovery_attempts ?? 0)],
  ["Raw physical turns", Number(session.raw_physical_turns ?? session.decisions ?? 0)],
  ["Simulation time (s)", session.simulation_time_s == null ? null : Number(session.simulation_time_s)],
  ["Calibration", session.calibration ?? ""],
  ["Checkpoint", session.checkpoint ?? ""],
];
sheet.getRange("E10").formulas = [["=IF(E7=0,\"\",E8/E7)"]];
sheet.getRange("E10").format.numberFormat = "0.0%";
sheet.getRange("B11:B12").format.numberFormat = "yyyy-mm-dd hh:mm:ss";
sheet.getRange("B13:E15").format.wrapText = false;
sheet.getRange("A6:A13").format = { fill: paleGray, font: { name: font, bold: true, color: text } };
sheet.getRange("D6:D15").format = { fill: paleRed, font: { name: font, bold: true, color: darkRed } };
sheet.getRange("A6:E15").format.verticalAlignment = "center";
sheet.getRange("A6:E15").format.font.name = font;
sheet.getRange("A16").values = [[
  "Scoring rule: only the first model decision at each planned corner counts; correction turns remain below for audit only.",
]];
sheet.getRange("A16").format.font = { name: font, size: 9, italic: true, color: "#5F6368" };

sheet.getRange("A17:O17").values = [[
  "Physical turn", "Corner", "Attempt", "Counted?", "Attempt role",
  "Intended", "Chosen", "Result", "Wait (s)", "Simulation time (s)",
  "Confidence", "Source", "EEG epoch / file", "Corner X (m)", "Corner Y (m)",
]];
sheet.getRange("A17:O17").format = {
  fill: darkRed,
  font: { name: font, size: 10, bold: true, color: white },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  borders: { preset: "inside", style: "thin", color: white },
};

const detailRows = corners.map((row) => [
  Number(row.decision ?? 0),
  Number(row.turn ?? 0),
  Number(row.corner_attempt ?? 1),
  row.scored ? "YES" : "NO",
  row.attempt_role ?? "",
  row.intended_dir ?? "",
  row.chosen_dir ?? "",
  String(row.result ?? "").toUpperCase(),
  row.wait_s == null ? null : Number(row.wait_s),
  row.sim_time_s == null ? null : Number(row.sim_time_s),
  row.confidence === "" || row.confidence == null ? null : Number(row.confidence),
  row.source ?? "",
  [row.epoch ?? "", row.file ?? ""].filter(Boolean).join(" · "),
  row.corner_x == null ? null : Number(row.corner_x),
  row.corner_y == null ? null : Number(row.corner_y),
]);
if (detailRows.length) {
  const last = 17 + detailRows.length;
  sheet.getRangeByIndexes(17, 0, detailRows.length, 15).values = detailRows;
  sheet.getRange(`I18:J${last}`).format.numberFormat = "0.00";
  sheet.getRange(`K18:K${last}`).format.numberFormat = "0.0%";
  sheet.getRange(`N18:O${last}`).format.numberFormat = "0.00";
  sheet.getRange(`H18:H${last}`).conditionalFormats.addCustom('=H18="CORRECT"', {
    fill: green, font: { color: "#166534", bold: true },
  });
  sheet.getRange(`H18:H${last}`).conditionalFormats.addCustom('=H18="WRONG"', {
    fill: paleRed, font: { color: darkRed, bold: true },
  });
  sheet.getRange(`A18:O${last}`).conditionalFormats.addCustom('=$D18="NO"', {
    fill: paleGray, font: { color: "#6B7280", italic: true },
  });
  sheet.getRange(`A18:O${last}`).format.borders = {
    bottom: { style: "thin", color: "#E5E7EB" },
  };
} else {
  sheet.getRange("A18:O18").values = [[
    "No completed corner decisions were found in the maze report.", "", "", "", "", "",
    "", "", "", "", "", "", "", "", "",
  ]];
  sheet.getRange("A18:O18").format = { fill: paleRed, font: { name: font, color: darkRed, italic: true } };
}

const filesHeaderRow = 20 + Math.max(detailRows.length, 1);
sheet.getRange(`A${filesHeaderRow}:C${filesHeaderRow}`).values = [["Recorded file", "Size (bytes)", "Purpose"]];
sheet.getRange(`A${filesHeaderRow}:C${filesHeaderRow}`).format = {
  fill: red, font: { name: font, bold: true, color: white }, horizontalAlignment: "center",
};
const fileRows = files.map((item) => [
  item.name,
  Number(item.size_bytes ?? 0),
  item.name.endsWith(".fif") ? "Raw EEG" :
    item.name === "camera.mp4" ? "Participant camera" :
    item.name === "maze.mp4" ? "Maze screen recording" :
    item.name.endsWith(".npz") ? "Replayable pose trace" :
    item.name.endsWith(".csv") ? "Decisions / maze report" : "Session provenance",
]);
if (fileRows.length) {
  sheet.getRangeByIndexes(filesHeaderRow, 0, fileRows.length, 3).values = fileRows;
  sheet.getRange(`B${filesHeaderRow + 1}:B${filesHeaderRow + fileRows.length}`).format.numberFormat = "#,##0";
}

const usedLastRow = filesHeaderRow + Math.max(fileRows.length, 1);
sheet.getRange(`A1:O${usedLastRow}`).format.font.name = font;
sheet.getRange(`A1:O${usedLastRow}`).format.verticalAlignment = "center";
sheet.getRange("A:O").format.autofitColumns();
sheet.getRange("A:A").format.columnWidth = 21;
sheet.getRange("B:B").format.columnWidth = 24;
sheet.getRange("D:D").format.columnWidth = 25;
sheet.getRange("E:E").format.columnWidth = 25;
sheet.getRange("F:H").format.columnWidth = 14;
sheet.getRange("I:K").format.columnWidth = 16;
sheet.getRange("L:L").format.columnWidth = 18;
sheet.getRange("M:M").format.columnWidth = 38;
workbook.recalculate();
const region = await workbook.inspect({
  kind: "region", sheetId: "Results summary", range: `A1:O${Math.min(usedLastRow, 45)}`,
  maxChars: 6000,
});
console.log(region.ndjson ?? region);
const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 }, maxChars: 2000,
});
console.log(errors.ndjson ?? errors);

await fs.mkdir(path.dirname(xlsxPath), { recursive: true });
if (previewPath) {
  const preview = await workbook.render({
    sheetName: "Results summary", autoCrop: "all", scale: 1, format: "png",
  });
  await fs.mkdir(path.dirname(previewPath), { recursive: true });
  await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(xlsxPath);
console.log(xlsxPath);
