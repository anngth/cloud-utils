_two_factor_auth_error() {
  print -u2 -- "2fa: $*"
  return 1
}

two_factor_auth() {
  emulate -L zsh

  [[ $# -eq 0 ]] || {
    _two_factor_auth_error "usage: 2fa"
    return 1
  }

  local cmd
  for cmd in date xxd openssl awk tr pbcopy; do
    command -v "$cmd" >/dev/null 2>&1 || {
      _two_factor_auth_error "missing required command: $cmd"
      return 1
    }
  done

  local input secret tty_fd
  { exec {tty_fd}<>/dev/tty } 2>/dev/null || {
    _two_factor_auth_error "interactive terminal required"
    return 1
  }

  print -n -u "$tty_fd" "Base32 secret: "
  IFS= read -rs -u "$tty_fd" input || {
    print -u "$tty_fd"
    exec {tty_fd}>&-
    _two_factor_auth_error "failed to read secret"
    return 1
  }
  print -u "$tty_fd"
  exec {tty_fd}>&-

  # Normalize Base32: remove whitespace, uppercase, then validate alphabet/padding.
  secret=$(printf '%s' "$input" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
  input=""
  [[ -n "$secret" ]] || {
    _two_factor_auth_error "empty secret"
    return 1
  }

  case "$secret" in
    (*[!ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=]*)
      _two_factor_auth_error "invalid Base32 character"
      return 1
      ;;
  esac

  local data padding pad_len rem
  if [[ "$secret" == *"="* ]]; then
    data=${secret%%=*}
    padding=${secret#$data}

    [[ -z "${padding//=/}" ]] || {
      _two_factor_auth_error "padding must appear only at the end"
      return 1
    }
    [[ $(( ${#secret} % 8 )) -eq 0 ]] || {
      _two_factor_auth_error "padded Base32 length must be a multiple of 8"
      return 1
    }

    pad_len=${#padding}
    rem=$(( ${#data} % 8 ))
    case "$rem:$pad_len" in
      (0:0|2:6|4:4|5:3|7:1) ;;
      (*)
        _two_factor_auth_error "invalid Base32 padding"
        return 1
        ;;
    esac
  else
    data=$secret
    rem=$(( ${#data} % 8 ))
    case "$rem" in
      (0|2|4|5|7) ;;
      (*)
        _two_factor_auth_error "invalid unpadded Base32 length"
        return 1
        ;;
    esac
  fi

  [[ -n "$data" ]] || {
    _two_factor_auth_error "empty Base32 data"
    return 1
  }

  local key_hex
  key_hex=$(
    printf '%s\n' "$data" | awk '
      BEGIN {
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        bits = 0
        buffer = 0
        pow2[0] = 1
        for (i = 1; i <= 16; i++) pow2[i] = pow2[i - 1] * 2
      }
      {
        for (i = 1; i <= length($0); i++) {
          c = substr($0, i, 1)
          v = index(alphabet, c) - 1
          if (v < 0) exit 2

          buffer = buffer * 32 + v
          bits += 5

          while (bits >= 8) {
            bits -= 8
            byte = int(buffer / pow2[bits])
            printf "%02x", byte
            buffer -= byte * pow2[bits]
          }
        }
      }
      END {
        if (bits > 0 && buffer != 0) exit 3
      }
    '
  ) || {
    secret=""
    data=""
    _two_factor_auth_error "invalid Base32 encoding"
    return 1
  }

  secret=""
  data=""

  [[ -n "$key_hex" ]] || {
    _two_factor_auth_error "decoded secret is empty"
    return 1
  }

  if [[ ${#key_hex} -gt 128 ]]; then
    key_hex=$(
      printf '%s' "$key_hex" |
        xxd -r -p |
        openssl dgst -sha1 -binary |
        xxd -p -c 256
    ) || {
      _two_factor_auth_error "failed to hash long HMAC key"
      return 1
    }
  fi

  # Create RFC 6238 moving counter as 8 bytes, big-endian.
  local time_step counter counter_hex
  time_step=30
  counter=$(( $(date +%s) / time_step ))
  counter_hex=$(printf '%016x' "$counter")

  # HMAC-SHA1: build ipad/opad without exposing the decoded key in process args.
  local hmac_pads inner_pad_hex outer_pad_hex inner_hash_hex hmac_hex
  hmac_pads=$(
    printf '%s\n' "$key_hex" | awk '
      BEGIN {
        hex = "0123456789abcdef"
      }
      function hexval(c) {
        return index(hex, tolower(c)) - 1
      }
      function hexbyte(n) {
        return substr(hex, int(n / 16) + 1, 1) substr(hex, (n % 16) + 1, 1)
      }
      function xor(a, b,    result, bit) {
        result = 0
        for (bit = 1; bit < 256; bit *= 2) {
          if ((int(a / bit) % 2) != (int(b / bit) % 2)) result += bit
        }
        return result
      }
      {
        key = tolower($0)
        for (i = 1; i <= 64; i++) {
          if ((i * 2) <= length(key)) {
            byte = hexval(substr(key, i * 2 - 1, 1)) * 16 + hexval(substr(key, i * 2, 1))
          } else {
            byte = 0
          }
          ipad = ipad hexbyte(xor(byte, 54))
          opad = opad hexbyte(xor(byte, 92))
        }
        print ipad
        print opad
      }
    '
  ) || {
    _two_factor_auth_error "failed to prepare HMAC pads"
    return 1
  }

  inner_pad_hex=${hmac_pads%%$'\n'*}
  outer_pad_hex=${hmac_pads#*$'\n'}

  inner_hash_hex=$(
    printf '%s' "${inner_pad_hex}${counter_hex}" |
      xxd -r -p |
      openssl dgst -sha1 -binary |
      xxd -p -c 256
  ) || {
    _two_factor_auth_error "failed to calculate HMAC-SHA1 inner hash"
    return 1
  }

  hmac_hex=$(
    printf '%s' "${outer_pad_hex}${inner_hash_hex}" |
      xxd -r -p |
      openssl dgst -sha1 -binary |
      xxd -p -c 256
  ) || {
    _two_factor_auth_error "failed to calculate HMAC-SHA1"
    return 1
  }

  [[ ${#hmac_hex} -eq 40 ]] || {
    _two_factor_auth_error "unexpected HMAC-SHA1 output"
    return 1
  }

  # Dynamic truncation per RFC 4226/RFC 6238, then reduce to 6 digits.
  local offset chunk code
  offset=$(( 16#${hmac_hex:39:1} ))
  chunk=${hmac_hex:$(( offset * 2 )):8}
  code=$(( (16#$chunk & 0x7fffffff) % 1000000 ))

  local otp
  otp=$(printf '%06d' "$code")
  printf '%s' "$otp" | pbcopy || {
    _two_factor_auth_error "failed to copy code to clipboard"
    return 1
  }

  printf 'Code copied: %s\n' "$otp"
}
