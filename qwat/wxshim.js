/* 定位注入层：在页面脚本执行前接管所有取点通道。
 * 占位符 __LAT__ __LNG__ __ACC__ 由 Python 侧替换。
 *
 * 覆盖四层：
 *   1. navigator.geolocation        —— 标准 Web API，桌面 Chrome 依赖 OS 位置服务，常报 POSITION_UNAVAILABLE
 *   2. 企业微信 JSSDK (wx.*)        —— wxwork 外调用 wx.getLocation 必定 fail，这是"定位服务未就绪"的高发原因
 *   3. WeixinJSBridge               —— 老页面走的桥接通道
 *   4. AMap / qq.maps 定位控件       —— 部分 H5 用地图 SDK 自己取点，内部仍可能走 IP 定位
 */
(function () {
  "use strict";

  var LAT = __LAT__;
  var LNG = __LNG__;
  var ACC = __ACC__;
  var TS = function () { return Date.now(); };

  // ---------- 1. navigator.geolocation ----------
  var pos = function () {
    return {
      coords: {
        latitude: LAT, longitude: LNG, accuracy: ACC,
        altitude: 12, altitudeAccuracy: 5, heading: null, speed: 0
      },
      timestamp: TS()
    };
  };
  var geo = {
    getCurrentPosition: function (ok) { setTimeout(function () { ok && ok(pos()); }, 60); },
    watchPosition: function (ok) { setTimeout(function () { ok && ok(pos()); }, 60); return 7; },
    clearWatch: function () {}
  };
  try {
    Object.defineProperty(navigator, "geolocation", { configurable: true, get: function () { return geo; } });
  } catch (e) { try { navigator.geolocation = geo; } catch (e2) {} }

  if (navigator.permissions && navigator.permissions.query) {
    var q = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = function (d) {
      if (d && /geolocation/i.test(d.name || "")) {
        return Promise.resolve({ state: "granted", onchange: null });
      }
      return q(d);
    };
  }

  // ---------- 2. 企微 JSSDK ----------
  var loc = function (type) {
    return {
      errMsg: "getLocation:ok",
      latitude: LAT, longitude: LNG, accuracy: ACC,
      speed: 0, altitude: 12,
      type: type || "wgs84"
    };
  };
  var okMsg = function (api) { return { errMsg: api + ":ok" }; };

  function harden(w) {
    if (!w || typeof w !== "object") { w = {}; }
    w.config = function (c) { try { c && c.success && c.success(okMsg("config")); } catch (e) {} };
    w.ready = function (cb) { try { cb && cb(); } catch (e) {} };
    w.error = function () {};
    w.checkJsApi = function (c) {
      try {
        var res = {};
        var js = (c && c.jsApiList) || [];
        for (var i = 0; i < js.length; i++) { res[js[i]] = true; }
        c && c.success && c.success({ errMsg: "checkJsApi:ok", checkResult: res });
      } catch (e) {}
    };
    w.getLocation = function (c) {
      try { c && c.success && c.success(loc(c && c.type)); }
      catch (e) { try { c && c.fail && c.fail({ errMsg: "getLocation:ok" }); } catch (e2) {} }
    };
    w.openLocation = function (c) { try { c && c.success && c.success(okMsg("openLocation")); } catch (e) {} };
    w.getNetworkType = function (c) { try { c && c.success && c.success({ errMsg: "getNetworkType:ok", networkType: "wifi" }); } catch (e) {} };
    w.startBeaconDiscovery = function (c) { try { c && c.success && c.success(okMsg("startBeaconDiscovery")); } catch (e) {} };
    w.stopBeaconDiscovery = function (c) { try { c && c.success && c.success(okMsg("stopBeaconDiscovery")); } catch (e) {} };
    w.getWXVerifyInfo = function (c) { try { c && c.success && c.success(okMsg("getWXVerifyInfo")); } catch (e) {} };
    return w;
  }

  var _wx = harden(window.wx);
  try {
    Object.defineProperty(window, "wx", {
      configurable: true,
      get: function () { return _wx; },
      set: function (v) { _wx = harden(v); }
    });
  } catch (e) { window.wx = _wx; }

  // ---------- 3. WeixinJSBridge ----------
  var bridge = {
    invoke: function (api, params, cb) {
      if (api === "getLocation") {
        cb && cb({ err_msg: "getLocation:ok", latitude: LAT, longitude: LNG, accuracy: ACC });
      } else {
        cb && cb({ err_msg: api + ":ok" });
      }
    },
    on: function () {},
    call: function () {}
  };
  try {
    Object.defineProperty(window, "WeixinJSBridge", {
      configurable: true,
      get: function () { return bridge; },
      set: function () {}
    });
  } catch (e) {}

  // ---------- 4. 地图 SDK 定位控件 ----------
  function patchAMap(AMap) {
    if (!AMap || !AMap.Geolocation) { return AMap; }
    var p = AMap.Geolocation.prototype;
    var result = { position: { lng: LNG, lat: LAT }, accuracy: ACC, location_type: "html5", info: "SUCCESS" };
    p.getCurrentPosition = function (cb) { setTimeout(function () { cb && cb("complete", result); }, 60); };
    p.watchPosition = function (cb) { setTimeout(function () { cb && cb("complete", result); }, 60); };
    return AMap;
  }
  function patchTMap(qq) {
    if (!qq || !qq.maps || !qq.maps.Geolocation) { return qq; }
    var p = qq.maps.Geolocation.prototype;
    p.getLocation = function (ok) {
      setTimeout(function () {
        ok && ok({
          lat: LAT, lng: LNG, accuracy: ACC, level: 16,
          addr: "__ADDRESS__", city: "深圳市"
        });
      }, 60);
    };
    return qq;
  }

  try {
    var _AMap = window.AMap;
    Object.defineProperty(window, "AMap", {
      configurable: true,
      get: function () { return _AMap; },
      set: function (v) { _AMap = patchAMap(v); }
    });
  } catch (e) {}

  try {
    var _qq = window.qq;
    Object.defineProperty(window, "qq", {
      configurable: true,
      get: function () { return _qq; },
      set: function (v) { _qq = patchTMap(v); }
    });
  } catch (e) {}

  // ---------- 5. 标记，便于诊断脚本自检 ----------
  window.__QW_GEO_SHIM__ = { lat: LAT, lng: LNG, acc: ACC, at: TS() };
})();
