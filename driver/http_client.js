// Simple HTTP Client Driver for RTI XP processors
// Sends configured HTTP request strings over plain TCP.

var MODULE_ID = "HTTPCLIENT";
var DRIVER_VERSION = "__VERSION__";

System.Print(MODULE_ID + ": Initializing Simple HTTP Client " + DRIVER_VERSION + "\r\n");
System.Print(MODULE_ID + ": Firmware " + System.FirmwareVersion + "\r\n");
System.Print(MODULE_ID + ": Runtime " + System.Version + "\r\n");

var g_debug = Config.Get("DebugTrace") == "true";
var g_defaultHost = Config.Get("defaultHost");
var g_defaultPort = parseInt(Config.Get("defaultPort"), 10);
var g_defaultMethod = Config.Get("defaultMethod");
var g_defaultHeaders = Config.Get("defaultHeaders");

if (isNaN(g_defaultPort) || g_defaultPort < 1 || g_defaultPort > 65535) {
	System.Print(MODULE_ID + ": Invalid default port; using 80\r\n");
	g_defaultPort = 80;
}
if (g_defaultMethod === "") {
	g_defaultMethod = "GET";
}

var g_comm = null;
var g_commSeq = 0;      // incremented each time a new TCP is opened; closures capture
                        // their own seq so stale callbacks from replaced connections are ignored
var g_activeHost = "";
var g_activePort = 0;

initializeSystemVariables();
dbgPrint("Initialization complete");

function SendConfiguredRequest(index) {
	var requestString = Config.Get("request" + index + "String");
	if (requestString === "") {
		setError("HTTP string " + index + " is empty");
		return;
	}
	SendHTTP(requestString);
}

function SendHTTP(httpString) {
	var decoded = decodeConfiguredText(httpString);
	var target;

	if (trimString(decoded) === "") {
		setError("Cannot send an empty HTTP string");
		return;
	}

	target = buildTarget(decoded);
	if (target === null) {
		return;
	}

	try {
		ensureClient(target.host, target.port);
		SystemVars.Write("LastError", "");
		SystemVars.Write("LastRequest", target.request);
		incrementCounter("TXCountSysvar");
		dbgPrint("Sending to " + target.host + ":" + target.port + " -> " + printable(target.request));
		g_comm.Write(target.request);
	} catch (err) {
		setError("Send failed: " + err);
	}
}

function buildTarget(value) {
	var raw = trimString(value);
	var rawHost;
	var parsed;
	var host;
	var port;
	var path;
	var request;

	if (isRawHttpRequest(raw)) {
		rawHost = getHostFromRawRequest(raw);
		host = rawHost.host;
		port = rawHost.port;

		if (host === "") {
			host = g_defaultHost;
			port = g_defaultPort;
		}
		if (host === "") {
			setError("Raw HTTP request needs a Host header or Default Host setting");
			return null;
		}

		request = normalizeNewlines(raw);
		if (request.indexOf("\r\n\r\n") < 0) {
			request += "\r\n\r\n";
		}
		return { host: host, port: port, request: request };
	}

	if (/^https:\/\//i.test(raw)) {
		setError("HTTPS is not supported by this simple TCP driver; use HTTP or a local proxy");
		return null;
	}

	parsed = raw.match(/^http:\/\/([^\/:?#]+)(:([0-9]+))?([\/?].*)?$/i);
	if (parsed !== null) {
		host = parsed[1];
		port = parsed[3] ? parseInt(parsed[3], 10) : 80;
		path = parsed[4] ? parsed[4] : "/";
	} else {
		host = g_defaultHost;
		port = g_defaultPort;
		path = raw;
	}

	if (host === "") {
		setError("Default Host is empty");
		return null;
	}
	if (isNaN(port) || port < 1 || port > 65535) {
		setError("Invalid HTTP port: " + port);
		return null;
	}
	if (path === "") {
		path = "/";
	}
	if (path.charAt(0) !== "/") {
		path = "/" + path;
	}

	request = buildSimpleRequest(host, port, path);
	return { host: host, port: port, request: request };
}

function buildSimpleRequest(host, port, path) {
	var hostHeader = host;
	var headers = normalizeHeaders(decodeConfiguredText(g_defaultHeaders));
	var request;

	if (port != 80) {
		hostHeader += ":" + port;
	}

	request = g_defaultMethod + " " + path + " HTTP/1.1\r\n";
	request += "Host: " + hostHeader + "\r\n";
	request += "Connection: close\r\n";
	if (headers !== "") {
		request += headers;
	}
	request += "\r\n";
	return request;
}

function ensureClient(host, port) {
	var seq;
	if (g_comm === null || g_activeHost !== host || g_activePort !== port) {
		seq = ++g_commSeq;
		g_comm = null;          // abandon old connection before creating new one
		g_activeHost = host;
		g_activePort = port;
		dbgPrint("Opening TCP client to " + host + ":" + port);
		g_comm = new TCP(
			function(data) { if (g_commSeq === seq) { OnCommRx(data); } },
			host, port
		);
		g_comm.OnConnectFunc    = function() { if (g_commSeq === seq) { OnTCPConnect();    } };
		g_comm.OnDisconnectFunc = function() { if (g_commSeq === seq) { OnTCPDisconnect(); } };
		try {
			if (g_comm.AddRxHTTPFraming) {
				g_comm.AddRxHTTPFraming();
			}
		} catch (err) {
			dbgPrint("AddRxHTTPFraming not available: " + err);
		}
	}
}

function OnTCPConnect() {
	dbgPrint("TCP connected");
	SystemVars.Write("Connected", true);
	System.SignalEvent("CONNECT");
}

function OnTCPDisconnect() {
	dbgPrint("TCP disconnected");
	SystemVars.Write("Connected", false);
	System.SignalEvent("DISCONNECT");
	g_comm = null;
}

function OnCommRx(data) {
	var str = data.toString();
	var status = parseStatusCode(str);

	dbgPrint("RX " + data.length + " bytes: " + printable(str));
	SystemVars.Write("LastResponse", System.ConvertFromUTF8(str));
	SystemVars.Write("LastStatusCode", status);
	incrementCounter("RXCountSysvar");
	System.SignalEvent("RESPONSE");
}

function decodeConfiguredText(value) {
	var text = value;
	if (text === null || typeof(text) == "undefined") {
		return "";
	}

	text = unescape(text.toString());
	text = text.replace(/\\r/g, "\r");
	text = text.replace(/\\n/g, "\n");
	text = text.replace(/\\t/g, "\t");
	text = text.replace(/\\x([0-9A-Fa-f]{2})/g, function(match, hex) {
		return String.fromCharCode(parseInt(hex, 16));
	});
	return text;
}

function isRawHttpRequest(value) {
	return /^[A-Z]+[ \t]+[^ \t\r\n]+[ \t]+HTTP\/1\.[01]/.test(value);
}

function getHostFromRawRequest(request) {
	var match = request.match(/(?:^|\r\n|\n)Host:[ \t]*([^:\r\n]+)(:([0-9]+))?/i);
	var port = g_defaultPort;

	if (match === null) {
		return { host: "", port: port };
	}
	if (match[3]) {
		port = parseInt(match[3], 10);
	}
	return { host: trimString(match[1]), port: port };
}

function normalizeHeaders(headers) {
	var normalized = normalizeNewlines(trimString(headers));
	if (normalized === "") {
		return "";
	}
	if (normalized.substring(normalized.length - 2) != "\r\n") {
		normalized += "\r\n";
	}
	return normalized;
}

function normalizeNewlines(value) {
	var result = value;
	result = result.replace(/\r\n/g, "\n");
	result = result.replace(/\r/g, "\n");
	result = result.replace(/\n/g, "\r\n");
	return result;
}

function parseStatusCode(response) {
	var match = response.match(/^HTTP\/\d+\.\d+\s+([0-9]+)/);
	if (match !== null) {
		return parseInt(match[1], 10);
	}
	return 0;
}

function initializeSystemVariables() {
	SystemVars.Write("Connected", false);
	SystemVars.Write("LastError", "");
	SystemVars.Write("LastRequest", "");
	SystemVars.Write("LastResponse", "");
	SystemVars.Write("LastStatusCode", 0);
	SystemVars.Write("TXCountSysvar", 0);
	SystemVars.Write("RXCountSysvar", 0);
}

function incrementCounter(name) {
	var current = parseInt(SystemVars.Read(name), 10);
	if (isNaN(current)) {
		current = 0;
	}
	SystemVars.Write(name, current + 1);
}

function setError(message) {
	System.Print(MODULE_ID + ": ERROR: " + message + "\r\n");
	SystemVars.Write("LastError", message);
	System.SignalEvent("ERROR");
}

function dbgPrint(message) {
	var splitLength = 150;
	var localMsg = message;
	if (!g_debug) {
		return;
	}
	while (localMsg.length > splitLength) {
		System.Print(MODULE_ID + ": " + localMsg.substring(0, splitLength) + "\r\n");
		localMsg = localMsg.substring(splitLength, localMsg.length);
	}
	System.Print(MODULE_ID + ": " + localMsg + "\r\n");
}

function trimString(value) {
	return value.replace(/^\s+|\s+$/g, "");
}

function printable(value) {
	return value.replace(/\r/g, "\\r").replace(/\n/g, "\\n");
}
