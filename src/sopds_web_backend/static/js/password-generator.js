/* Генератор паролей по требованиям сайта (sopds_web_backend/password_rules.py):
 * 16 символов — строчные, заглавные, цифры и спецсимволы, хотя бы по одному
 * из каждой группы; без похожих символов (l/1/I, O/0). Пароль создаётся в
 * браузере (crypto.getRandomValues) и уходит на сервер только с формой.
 *
 * Кнопка: <button type="button" onclick="sopdsFillPassword(this)"
 *          data-targets="id1 id2" data-show="id-подписи">
 */
(function () {
    var SETS = ["abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "!#$%&*+-=?@^_"];

    function randomIndex(n) {
        var a = new Uint32Array(1);
        window.crypto.getRandomValues(a);
        return a[0] % n;
    }

    window.sopdsGeneratePassword = function (length) {
        length = length || 16;
        var all = SETS.join("");
        var chars = SETS.map(function (set) { return set[randomIndex(set.length)]; });
        while (chars.length < length) chars.push(all[randomIndex(all.length)]);
        for (var i = chars.length - 1; i > 0; i--) {          // перемешать (Фишер–Йетс)
            var j = randomIndex(i + 1), t = chars[i];
            chars[i] = chars[j];
            chars[j] = t;
        }
        return chars.join("");
    };

    window.sopdsFillPassword = function (button) {
        var password = window.sopdsGeneratePassword(16);
        (button.dataset.targets || "").split(/\s+/).forEach(function (id) {
            var input = id && document.getElementById(id);
            if (input) {
                input.value = password;
                input.type = "text";                           // видно — чтобы скопировать и передать
            }
        });
        var shown = button.dataset.show && document.getElementById(button.dataset.show);
        if (shown) {
            shown.textContent = password;
            shown.style.display = "";
        }
    };
})();
