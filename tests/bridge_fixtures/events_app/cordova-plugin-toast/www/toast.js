var exec = require('cordova/exec');

exports.show = function (msg, ok, fail) {
  exec(ok, fail, 'Toast', 'show', [msg]);
};

exports.hide = function (ok, fail) {
  cordova.exec(ok, fail, 'Toast', 'hide', []);
};

exports.vibrate = function (ok, fail) {
  exec(ok, fail, 'Toast', 'vibrate', []);
};
