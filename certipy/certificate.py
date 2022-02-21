from typing import Tuple

from asn1crypto import cms as asn1cms
from asn1crypto import core as asn1core
from asn1crypto import x509 as asn1x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from cryptography.x509.oid import ExtensionOID, NameOID
from impacket.dcerpc.v5.nrpc import checkNullString
from pyasn1.codec.der import decoder, encoder
from pyasn1.type.char import UTF8String

PRINCIPAL_NAME = x509.ObjectIdentifier("1.3.6.1.4.1.311.20.2.3")


class EnrollmentNameValuePair(asn1core.Sequence):
    _fields = [
        ("name", asn1core.BMPString),
        ("value", asn1core.BMPString),
    ]


class EnrollmentNameValuePairs(asn1core.SetOf):
    _child_spec = EnrollmentNameValuePair


def csr_to_der(csr: x509.CertificateSigningRequest) -> bytes:
    return csr.public_bytes(Encoding.DER)


def csr_to_pem(csr: x509.CertificateSigningRequest) -> bytes:
    return csr.public_bytes(Encoding.PEM)


def cert_to_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(Encoding.PEM)


def cert_to_der(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(Encoding.DER)


def key_to_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, encryption_algorithm=NoEncryption()
    )


def der_to_key(key: bytes) -> rsa.RSAPrivateKey:
    return serialization.load_der_private_key(key, None)


def pem_to_key(key: bytes) -> rsa.RSAPrivateKey:
    return serialization.load_pem_private_key(key, None)


def der_to_cert(certificate: bytes) -> x509.Certificate:
    return x509.load_der_x509_certificate(certificate)


def pem_to_cert(certificate: bytes) -> x509.Certificate:
    return x509.load_pem_x509_certificate(certificate)


def get_id_from_certificate(
    certificate: x509.Certificate,
) -> Tuple[str, str]:
    try:
        san = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        )

        for name in san.value.get_values_for_type(x509.OtherName):
            if name.type_id == PRINCIPAL_NAME:
                return (
                    "UPN",
                    decoder.decode(name.value, asn1Spec=UTF8String)[0].decode(),
                )

        for name in san.value.get_values_for_type(x509.DNSName):
            return "DNS Host Name", name
    except:
        pass

    return None, None


def create_pfx(key: rsa.RSAPrivateKey, cert: x509.Certificate) -> bytes:
    return pkcs12.serialize_key_and_certificates(
        name=b"",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=NoEncryption(),
    )


def load_pfx(
    pfx: bytes, password: bytes = None
) -> Tuple[rsa.RSAPrivateKey, x509.Certificate, None]:
    return pkcs12.load_key_and_certificates(pfx, password)[:-1]


def generate_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=0x10001, key_size=2048)


def create_csr(
    username: str, alt_name: bytes = None, key: rsa.RSAPrivateKey = None
) -> Tuple[x509.CertificateSigningRequest, rsa.RSAPrivateKey]:
    if key is None:
        key = generate_rsa_key()

    csr = x509.CertificateSigningRequestBuilder()

    csr = csr.subject_name(
        x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, username),
            ]
        )
    )

    if alt_name:
        if type(alt_name) == str:
            alt_name = alt_name.encode()
        alt_name = encoder.encode(UTF8String(alt_name))

        csr = csr.add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.OtherName(PRINCIPAL_NAME, alt_name),
                ]
            ),
            critical=False,
        )

    return (csr.sign(key, hashes.SHA256()), key)


def rsa_pkcs1v15_sign(
    data: bytes, key: rsa.RSAPrivateKey, hash: hashes.HashAlgorithm = hashes.SHA256
):
    return key.sign(data, padding.PKCS1v15(), hash())


def hash_digest(data: bytes, hash: hashes.Hash):
    digest = hashes.Hash(hash())
    digest.update(data)
    return digest.finalize()


def create_cms(
    request: bytes, on_behalf_of: str, cert: x509.Certificate, key: rsa.RSAPrivateKey
):
    cert = asn1x509.Certificate.load(cert_to_der(cert))
    content_info = asn1cms.ContentInfo()
    content_info["content_type"] = "data"
    content_info["content"] = request

    issuer_and_serial = asn1cms.IssuerAndSerialNumber()
    issuer_and_serial["issuer"] = cert.issuer
    issuer_and_serial["serial_number"] = cert.serial_number

    digest_algorithm = asn1cms.DigestAlgorithm()
    digest_algorithm["algorithm"] = "sha256"

    name_value_pairs = EnrollmentNameValuePairs()
    requester_name = EnrollmentNameValuePair()
    requester_name["name"] = checkNullString("requestername")
    requester_name["value"] = checkNullString(on_behalf_of)
    name_value_pairs.append(requester_name)
    name_value_pairs_attrib = asn1cms.CMSAttribute()
    asn1cms.CMSAttribute._oid_specs["1.3.6.1.4.1.311.13.2.1"] = EnrollmentNameValuePairs
    name_value_pairs_attrib["type"] = "1.3.6.1.4.1.311.13.2.1"
    name_value_pairs_attrib["values"] = name_value_pairs

    signed_attribs = asn1cms.CMSAttributes()
    signed_attribs.append(name_value_pairs_attrib)
    att = asn1cms.CMSAttribute()
    att["type"] = "message_digest"
    att["values"] = [hash_digest(request, hashes.SHA256)]
    signed_attribs.append(att)

    attribs_signature = rsa_pkcs1v15_sign(signed_attribs.dump(), key)

    signer_info = asn1cms.SignerInfo()
    signer_info["version"] = 1
    signer_info["sid"] = issuer_and_serial
    signer_info["digest_algorithm"] = digest_algorithm
    signer_info["signature_algorithm"] = cert["signature_algorithm"]
    signer_info["signature"] = attribs_signature
    signer_info["signed_attrs"] = signed_attribs

    signed_info_attribs = asn1cms.SignerInfo()
    signed_info_attribs["version"] = 1
    signed_info_attribs["sid"] = issuer_and_serial
    signed_info_attribs["digest_algorithm"] = digest_algorithm
    signed_info_attribs["signature_algorithm"] = cert["signature_algorithm"]
    signed_info_attribs["signature"] = attribs_signature
    signed_info_attribs["signed_attrs"] = signed_attribs

    signer_infos = asn1cms.SignerInfos()
    signer_infos.append(signer_info)

    digest_algorithms = asn1cms.DigestAlgorithms()
    digest_algorithms.append(digest_algorithm)

    certificate_choice = asn1cms.CertificateChoices("certificate", cert)
    certificate_set = asn1cms.CertificateSet()
    certificate_set.append(certificate_choice)

    signed_data = asn1cms.SignedData()
    signed_data["version"] = 1
    signed_data["digest_algorithms"] = digest_algorithms
    signed_data["encap_content_info"] = content_info
    signed_data["certificates"] = certificate_set
    signed_data["signer_infos"] = signer_infos

    outer_content_info = asn1cms.ContentInfo()
    outer_content_info["content_type"] = "signed_data"
    outer_content_info["content"] = signed_data

    return outer_content_info.dump()