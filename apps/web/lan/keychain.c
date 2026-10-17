#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc != 3 || (strcmp(argv[1], "store") && strcmp(argv[1], "read") && strcmp(argv[1], "delete"))) {
        fprintf(stderr, "usage: keychain-helper store|read|delete account\n");
        return 2;
    }
    CFMutableDictionaryRef query = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFStringRef service = CFSTR("CharacterStudio.LocalCA.Passphrase");
    CFStringRef account = CFStringCreateWithCString(NULL, argv[2], kCFStringEncodingUTF8);
    if (!query || !account) return 2;
    CFDictionarySetValue(query, kSecClass, kSecClassGenericPassword);
    CFDictionarySetValue(query, kSecAttrService, service);
    CFDictionarySetValue(query, kSecAttrAccount, account);
    CFDictionarySetValue(query, kSecAttrSynchronizable, kCFBooleanFalse);
    OSStatus status;
    if (!strcmp(argv[1], "store")) {
        unsigned char secret[512];
        size_t count = fread(secret, 1, sizeof(secret), stdin);
        if (count < 32 || count == sizeof(secret)) {
            fprintf(stderr, "invalid secret length\n");
            return 2;
        }
        CFDataRef data = CFDataCreate(NULL, secret, (CFIndex)count);
        CFDictionarySetValue(query, kSecValueData, data);
        status = SecItemAdd(query, NULL);
        memset(secret, 0, sizeof(secret));
        CFRelease(data);
    } else if (!strcmp(argv[1], "read")) {
        CFDictionarySetValue(query, kSecReturnData, kCFBooleanTrue);
        CFDictionarySetValue(query, kSecMatchLimit, kSecMatchLimitOne);
        CFTypeRef result = NULL;
        status = SecItemCopyMatching(query, &result);
        if (status == errSecSuccess && result) {
            CFDataRef data = (CFDataRef)result;
            fwrite(CFDataGetBytePtr(data), 1, (size_t)CFDataGetLength(data), stdout);
            CFRelease(result);
        }
    } else {
        status = SecItemDelete(query);
    }
    CFRelease(account);
    CFRelease(query);
    if (status != errSecSuccess) {
        fprintf(stderr, "Mac Keychain operation failed (OSStatus %d)\n", (int)status);
        return 1;
    }
    return 0;
}
