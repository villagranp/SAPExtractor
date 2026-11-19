"""Parser de ``$metadata`` V2 (SEGW ODP / @OData.publish) y V4 (service binding RAP)."""

from __future__ import annotations

import pytest

from sapextract.sap_connections.domain.errors import SapResponseError
from sapextract.sap_connections.domain.metadata import OdpRole
from sapextract.sap_connections.infrastructure.metadata_parser import parse_metadata

V2_ODP = b"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="1.0" xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"
  xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
  xmlns:sap="http://www.sap.com/Protocols/SAPData">
 <edmx:DataServices m:DataServiceVersion="2.0">
  <Schema Namespace="ZFI_WS_GL_ITEMS_SRV" xmlns="http://schemas.microsoft.com/ado/2008/09/edm">
   <EntityType Name="EntityOf0FI_GL_14Type">
    <Key><PropertyRef Name="BELNR"/><PropertyRef Name="BUZEI"/></Key>
    <Property Name="BELNR" Type="Edm.String" Nullable="false" MaxLength="10" sap:label="Documento"/>
    <Property Name="BUZEI" Type="Edm.String" Nullable="false" MaxLength="3" sap:label="Pos."/>
    <Property Name="BUDAT" Type="Edm.DateTime" sap:display-format="Date" sap:label="Fecha"/>
    <Property Name="DMBTR" Type="Edm.Decimal" Precision="17" Scale="2" sap:label="Importe"/>
    <Property Name="ODQ_CHANGEMODE" Type="Edm.String" MaxLength="1" sap:filterable="false"/>
   </EntityType>
   <EntityType Name="DeltaLinksOfEntityOf0FI_GL_14Type">
    <Key><PropertyRef Name="DeltaToken"/></Key>
    <Property Name="DeltaToken" Type="Edm.String" Nullable="false"/>
   </EntityType>
   <EntityContainer Name="C" m:IsDefaultEntityContainer="true">
    <EntitySet Name="EntityOf0FI_GL_14" EntityType="ZFI_WS_GL_ITEMS_SRV.EntityOf0FI_GL_14Type"
      sap:pageable="true" sap:change-tracking="true" sap:label="Partidas"/>
    <EntitySet Name="DeltaLinksOfEntityOf0FI_GL_14"
      EntityType="ZFI_WS_GL_ITEMS_SRV.DeltaLinksOfEntityOf0FI_GL_14Type"/>
   </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""

V4_RAP = b"""<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
 <edmx:DataServices>
  <Schema Namespace="com.sap.gateway.srvd.zmm_material.v0001"
    xmlns="http://docs.oasis-open.org/odata/ns/edm">
   <EntityType Name="MaterialType">
    <Key><PropertyRef Name="Material"/></Key>
    <Property Name="Material" Type="Edm.String" Nullable="false" MaxLength="40"/>
    <Property Name="ChangedAt" Type="Edm.DateTimeOffset" Precision="7"/>
   </EntityType>
   <EntityContainer Name="Container">
    <EntitySet Name="Material" EntityType="com.sap.gateway.srvd.zmm_material.v0001.MaterialType"/>
   </EntityContainer>
   <Annotations Target="com.sap.gateway.srvd.zmm_material.v0001.MaterialType/Material">
    <Annotation Term="SAP__common.Label" String="Material"/>
   </Annotations>
   <Annotations Target="com.sap.gateway.srvd.zmm_material.v0001.Container/Material">
    <Annotation Term="SAP__capabilities.ChangeTracking">
     <Record><PropertyValue Property="Supported" Bool="true"/></Record>
    </Annotation>
   </Annotations>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>"""


def test_parses_v2_odp_service() -> None:
    meta = parse_metadata(V2_ODP)
    assert meta.odata_version == "2.0"
    assert [s.name for s in meta.entity_sets] == [
        "EntityOf0FI_GL_14",
        "DeltaLinksOfEntityOf0FI_GL_14",
    ]
    main = meta.entity_sets[0]
    assert main.odp_role is OdpRole.ENTITY
    assert main.change_tracking
    assert main.label == "Partidas"
    assert meta.supports_delta(main)
    et = meta.entity_type_of(main)
    assert et is not None
    assert et.keys == ("BELNR", "BUZEI")
    props = {p.name: p for p in et.properties}
    assert props["BELNR"].is_key
    assert not props["BELNR"].nullable
    assert props["BELNR"].max_length == 10
    assert props["BELNR"].label == "Documento"
    assert (props["DMBTR"].precision, props["DMBTR"].scale) == (17, 2)
    assert props["BUDAT"].display_format == "Date"
    assert props["ODQ_CHANGEMODE"].filterable is False


def test_parses_v4_with_external_annotations() -> None:
    meta = parse_metadata(V4_RAP)
    assert meta.odata_version == "4.0"
    (entity_set,) = meta.entity_sets
    assert entity_set.change_tracking
    et = meta.entity_type_of(entity_set)
    assert et is not None
    labels = {p.name: p.label for p in et.properties}
    assert labels == {"Material": "Material", "ChangedAt": None}
    assert next(p for p in et.properties if p.name == "ChangedAt").precision == 7


def test_rejects_non_edmx_and_broken_xml() -> None:
    with pytest.raises(SapResponseError):
        parse_metadata(b"<html><body>Login</body></html>")
    with pytest.raises(SapResponseError):
        parse_metadata(b"<edmx:Edmx")


def test_rejects_entity_expansion_attacks() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        b'<Edmx xmlns="http://schemas.microsoft.com/ado/2007/06/edmx">&b;</Edmx>'
    )
    with pytest.raises(SapResponseError):
        parse_metadata(bomb)
    external = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        b'<Edmx xmlns="http://schemas.microsoft.com/ado/2007/06/edmx">&e;</Edmx>'
    )
    with pytest.raises(SapResponseError):
        parse_metadata(external)
